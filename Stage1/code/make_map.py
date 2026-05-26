"""Generate a geographic HTML map of all physical nodes + flow edges.

Output: oil_network_map.html in the same directory.

 * Plots every physical asset that has lat/lon coordinates
 * Colour-codes by node_subtype
 * Draws the directed flow graph from v_flow_edges (physical -> physical only)
 * Hover-tooltip per node shows: asset_id, subtype, PADD/state, # variables, # TS-bound
 * Self-contained: Plotly.js loaded from CDN
"""
from __future__ import annotations
from paths import HTML_DIR

import json
from collections import defaultdict
from pathlib import Path

import psycopg2

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_map.html"

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


SUBTYPE_COLOR = {
    "state_conventional":    "#1f77b4",  # production
    "state_sub_basin":       "#1f77b4",
    "state_residual":        "#1f77b4",
    "offshore_region":       "#1f77b4",
    "gathering":             "#aec7e8",
    "origin_terminal":       "#9467bd",
    "storage_terminal":      "#ff7f0e",  # hubs
    "spr_site":              "#d62728",  # SPR
    "pipeline":              "#7f7f7f",
    "refinery":              "#2ca02c",  # refineries
    "import_terminal":       "#8c564b",
    "export_terminal":       "#e377c2",
    "foreign_export_destination": "#bcbd22",
    "foreign_production_aggregate": "#17becf",
}
SUBTYPE_LABEL = {
    "state_conventional": "Production (state)",
    "state_sub_basin":    "Production (sub-basin)",
    "state_residual":     "Production (residual)",
    "offshore_region":    "Production (offshore)",
    "gathering":          "Gathering",
    "origin_terminal":    "Origin terminal",
    "storage_terminal":   "Storage hub",
    "spr_site":           "SPR site",
    "pipeline":           "Pipeline",
    "refinery":           "Refinery",
    "import_terminal":    "Import terminal",
    "export_terminal":    "Export terminal",
    "foreign_export_destination":   "Foreign export sink",
    "foreign_production_aggregate": "Foreign supply",
}


def fetch():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # ALL physical assets (with or without coords)
        cur.execute("""
            SELECT a.asset_id, a.name, a.node_subtype,
                   l.lat, l.lon, l.padd, l.state
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.kind = 'physical'
            ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        nodes = [dict(zip(cols, r)) for r in cur.fetchall()]

        # Per-node variable + TS counts
        cur.execute("""
            SELECT v.node_id, COUNT(*) AS n_vars,
                   COUNT(*) FILTER (WHERE va.timeseries_id IS NOT NULL) AS n_ts
            FROM oil_network.variables v
            LEFT JOIN oil_network.variable_assignments va USING (variable_id)
            GROUP BY v.node_id
        """)
        counts = {row[0]: {"n_vars": row[1], "n_ts": row[2]} for row in cur.fetchall()}

        # All physical-to-physical flow edges (we'll filter to plottable ones later)
        cur.execute("""
            SELECT e.source, e.target, e.commodity, e.via_variable
            FROM oil_network.v_flow_edges e
            JOIN oil_network.assets sa ON sa.asset_id = e.source
            JOIN oil_network.assets ta ON ta.asset_id = e.target
            WHERE sa.kind = 'physical' AND ta.kind = 'physical'
        """)
        edges = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    # Infer coordinates for nodes that have none (mainly pipelines) by averaging
    # their connected neighbours' coords. Iterate to handle pipeline-pipeline edges.
    coords = {n["asset_id"]: (n["lat"], n["lon"]) for n in nodes if n["lat"] is not None}
    neighbours = defaultdict(set)
    for e in edges:
        neighbours[e["source"]].add(e["target"])
        neighbours[e["target"]].add(e["source"])

    # Up to 3 passes — should converge quickly for a graph of this size
    for _pass in range(3):
        changed = False
        for n in nodes:
            nid = n["asset_id"]
            if nid in coords:
                continue
            neighbour_coords = [coords[nb] for nb in neighbours.get(nid, []) if nb in coords]
            if neighbour_coords:
                lat = sum(c[0] for c in neighbour_coords) / len(neighbour_coords)
                lon = sum(c[1] for c in neighbour_coords) / len(neighbour_coords)
                coords[nid] = (lat, lon)
                n["lat"], n["lon"] = lat, lon
                n["inferred_coords"] = True
                changed = True
        if not changed:
            break

    # Final filter: nodes that still lack coords are skipped from the map
    return nodes, counts, edges


def build_payload(nodes, counts, edges, node_vars=None):
    """Build the JSON payload embedded in the HTML.

    node_vars (optional): dict mapping asset_id -> list of variable dicts with
    keys {type, related, kind, detail, value, source}. When present, each node
    in the output payload carries a `variables` array so the click-side panel
    can list them without a runtime DB query.
    """
    by_id = {n["asset_id"]: n for n in nodes}

    # Group nodes by subtype for the trace structure (one trace per subtype = clean legend)
    nodes_by_sub = defaultdict(list)
    for n in nodes:
        if n.get("lat") is None or n.get("lon") is None:
            continue  # still uncoordinatable -- skip
        c = counts.get(n["asset_id"], {"n_vars": 0, "n_ts": 0})
        entry = {
            "asset_id": n["asset_id"],
            "name":     n["name"],
            "lat":      n["lat"],
            "lon":      n["lon"],
            "padd":     n["padd"],
            "state":    n["state"],
            "n_vars":   c["n_vars"],
            "n_ts":     c["n_ts"],
            "inferred": bool(n.get("inferred_coords")),
        }
        if node_vars is not None:
            entry["variables"] = node_vars.get(n["asset_id"], [])
        nodes_by_sub[n["node_subtype"]].append(entry)

    # Edges as lat/lon segments with per-point metadata so hover/click works.
    edge_segs = []
    for e in edges:
        s = by_id.get(e["source"])
        t = by_id.get(e["target"])
        if not s or not t:
            continue
        if s.get("lat") is None or t.get("lat") is None:
            continue
        label = f"{e['source']} -> {e['target']}"
        edge_segs.append({
            "source": e["source"],
            "target": e["target"],
            "commodity": e.get("commodity"),
            "via_variable": e.get("via_variable"),
            "label": label,
            "lat":   [s["lat"], t["lat"], None],
            "lon":   [s["lon"], t["lon"], None],
        })

    return {"nodes_by_sub": nodes_by_sub, "edges": edge_segs}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>oil_network &mdash; geographic map</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { margin: 0; font: 13px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #f7f7f5; color: #222; }
  header { padding: 8px 16px; background: #1a1f2c; color: #fff;
           display: flex; gap: 16px; align-items: center; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; }
  header .stats { margin-left: auto; font-size: 11.5px; color: #ccc; }
  .layout { display: grid; grid-template-columns: 1fr 320px 420px; height: calc(100vh - 38px); }
  #map { width: 100%; height: 100%; }
  #side { background: #ffffff; border-left: 1px solid #d0d0c8;
          padding: 14px 16px; overflow-y: auto; font-size: 12.5px; }
  #plot-panel { background: #ffffff; border-left: 1px solid #d0d0c8;
                padding: 12px 12px 8px; display: flex; flex-direction: column;
                font-size: 12.5px; min-width: 0; }
  #plot-panel h2 { margin: 0 0 4px 0; font-size: 13px; font-family: ui-monospace, Menlo, monospace;
                   word-break: break-all; color: #222; }
  #plot-panel .sub { font-size: 11px; color: #777; margin-bottom: 8px; }
  #plot-panel .placeholder { color: #999; padding: 60px 12px; text-align: center; font-style: italic;
                             font-size: 12px; flex: 1; }
  #plot-panel #plot { flex: 1; min-height: 0; }
  #side h2 { margin: 0 0 6px 0; font-size: 14px;
             font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  #side h3 { margin: 12px 0 4px 0; font-size: 12px; color: #666; text-transform: uppercase;
             letter-spacing: 0.04em; }
  #side .row { display: grid; grid-template-columns: 90px 1fr; gap: 4px 8px; margin-bottom: 2px; }
  #side .row .k { color: #777; }
  #side .row .v { font-family: ui-monospace, Menlo, monospace; word-break: break-all; }
  #side .arrow { font-size: 14px; color: #555; margin: 4px 0 4px 0; }
  #side .placeholder { color: #999; padding: 60px 0; text-align: center; font-style: italic; }
  #side .kind {
    display: inline-block; padding: 1px 6px; border-radius: 2px; font-size: 10px;
    color: white; margin-left: 4px; vertical-align: middle;
  }
  /* Variable list inside the side panel */
  #side .vargroup { margin: 8px 0 2px 0; font-size: 11px; font-weight: 600;
                    color: #444; text-transform: uppercase; letter-spacing: 0.04em; }
  #side .var { font-family: ui-monospace, Menlo, monospace; font-size: 11px;
               padding: 3px 0; border-bottom: 1px dotted #eee; cursor: pointer;
               display: grid; grid-template-columns: 22px 1fr auto; gap: 4px; align-items: baseline; }
  #side .var:hover { background: #f3f6fb; }
  #side .var.selected { background: #e1ebfa; }
  #side .var.no-series { cursor: default; opacity: 0.6; }
  #side .var.no-series:hover { background: transparent; }
  #side .var .badge { font-size: 9px; font-weight: 600; text-align: center;
                      border-radius: 2px; padding: 1px 0; color: white; line-height: 12px; }
  #side .var .badge.ts { background: #1f6feb; }
  #side .var .badge.f  { background: #888; }
  #side .var .badge.l  { background: #d97706; }   /* latent */
  #side .var .badge.x  { background: #ccc; color: #555; }  /* default / none */
  #side .var .name { color: #333; word-break: break-all; }
  #side .var .name .arrow-rel { color: #999; margin: 0 3px; }
  #side .var .name .detail { color: #888; font-size: 10px; display: block; margin-top: 1px; }
  #side .var .val { color: #111; font-weight: 600; white-space: nowrap; }
  #side .var .val.null { color: #d97706; font-weight: 400; font-style: italic; }
  #side .vardate { font-size: 10px; color: #999; margin-top: 4px; font-style: italic; }
</style>
</head>
<body>
<header>
  <h1>oil_network &middot; physical-asset geographic map</h1>
  <div class="stats">
    <b>__N_NODES__</b> physical nodes &middot;
    <b>__N_EDGES__</b> flow edges &middot;
    __N_SUBTYPES__ subtypes
  </div>
</header>
<div class="layout">
  <div id="map"></div>
  <aside id="side">
    <div class="placeholder">Click a node or an edge on the map.</div>
  </aside>
  <aside id="plot-panel">
    <div class="placeholder">Click a variable in the middle panel to see its time series.</div>
    <div id="plot" style="display:none;"></div>
  </aside>
</div>
<script>
const DATA = __DATA__;
const NODES_BY_SUB = DATA.nodes_by_sub;
const EDGES = DATA.edges;
const DATES = DATA.dates || [];      // shared x-axis for all time-series plots
const SERIES = DATA.series || {};    // variable_id -> {values, sources}

const SUBTYPE_COLOR = __SUBTYPE_COLOR__;
const SUBTYPE_LABEL = __SUBTYPE_LABEL__;

// Build a fast lookup of every node by asset_id (for the click handler)
const NODE_BY_ID = {};
for (const sub of Object.keys(NODES_BY_SUB)) {
  for (const n of NODES_BY_SUB[sub]) {
    NODE_BY_ID[n.asset_id] = Object.assign({subtype: sub}, n);
  }
}

const traces = [];

// ---- Edges trace (lines only — visual) ----
// One concatenated trace with None separators between segments. No markers here:
// plotly_click does not fire on bare line segments, only on actual data points.
const edgeLat = [], edgeLon = [];
for (const e of EDGES) {
  edgeLat.push(e.lat[0], e.lat[1], e.lat[2]);
  edgeLon.push(e.lon[0], e.lon[1], e.lon[2]);
}
traces.push({
  type: "scattergeo",
  mode: "lines",
  lon: edgeLon, lat: edgeLat,
  line: { width: 0.8, color: "rgba(60, 60, 60, 0.4)" },
  hoverinfo: "skip",
  name: "Flow edges",
  showlegend: true,
});

// ---- Invisible midpoint markers (clickable + hoverable) ----
// For every edge, place an invisible marker at the segment midpoint so users
// can click on a line and trigger plotly_click. Marker has zero visual opacity
// but a large hit-area so it picks up clicks anywhere near the midpoint.
const midLat = [], midLon = [], midCustom = [], midHover = [];
for (const e of EDGES) {
  midLat.push((e.lat[0] + e.lat[1]) / 2);
  midLon.push((e.lon[0] + e.lon[1]) / 2);
  midCustom.push({ kind: "edge", source: e.source, target: e.target,
                   commodity: e.commodity, via: e.via_variable });
  midHover.push(`<b>${e.source} → ${e.target}</b><br>${e.commodity || ""}<br><i>click for details</i>`);
}
traces.push({
  type: "scattergeo",
  mode: "markers",
  lon: midLon, lat: midLat,
  hovertext: midHover,
  hoverinfo: "text",
  customdata: midCustom,
  marker: { size: 14, color: "rgba(0,0,0,0)", line: { width: 0 }, opacity: 0.001 },
  name: "edge midpoints",
  showlegend: false,
});

// ---- One markers trace per subtype ----
const subOrder = Object.keys(SUBTYPE_COLOR);
for (const sub of subOrder) {
  const nodes = NODES_BY_SUB[sub] || [];
  if (nodes.length === 0) continue;
  traces.push({
    type: "scattergeo",
    mode: "markers",
    lat: nodes.map(n => n.lat),
    lon: nodes.map(n => n.lon),
    text: nodes.map(n =>
      `<b>${n.asset_id}</b><br>${n.name || ''}<br>` +
      `${SUBTYPE_LABEL[sub]}<br>` +
      `${n.padd || ''} ${n.state ? '/ ' + n.state : ''}<br>` +
      `${n.n_vars} variables, ${n.n_ts} TS-bound`
    ),
    hoverinfo: "text",
    customdata: nodes.map(n => ({kind: "node", asset_id: n.asset_id})),
    marker: {
      size: 9, color: SUBTYPE_COLOR[sub],
      line: { width: 0.6, color: "rgba(0,0,0,0.5)" },
    },
    name: SUBTYPE_LABEL[sub] + " (" + nodes.length + ")",
  });
}

const layout = {
  margin: { l: 0, r: 0, t: 0, b: 0 },
  geo: {
    scope: "north america",
    // 'natural earth' (or 'equirectangular') keeps Alaska at its real geographic
    // position instead of insetting it like 'albers usa' does.
    projection: { type: "natural earth" },
    showland: true,
    landcolor: "#f4f1ed",
    countrycolor: "#999",
    showcoastlines: true,
    coastlinecolor: "#999",
    showsubunits: true,
    subunitcolor: "#ccc",
    resolution: 50,
    lonaxis: { range: [-172, -55] },
    lataxis: { range: [18, 75] },
    showocean: true,
    oceancolor: "#e9eef3",
    showlakes: false,
  },
  legend: {
    bgcolor: "rgba(255,255,255,0.92)",
    bordercolor: "#ccc",
    borderwidth: 1,
    x: 0.005, y: 0.995, xanchor: "left", yanchor: "top",
    font: { size: 11 },
  },
};

const mapDiv = document.getElementById("map");
Plotly.newPlot(mapDiv, traces, layout, { responsive: true });

// ---- Click + hover handlers ----
function renderEdge(cd) {
  const sNode = NODE_BY_ID[cd.source] || {};
  const tNode = NODE_BY_ID[cd.target] || {};
  const side = document.getElementById("side");
  side.innerHTML =
    `<h3>Edge</h3>
     <h2>${cd.source}<br><span class="arrow">↓</span><br>${cd.target}</h2>
     <div class="row"><div class="k">commodity</div><div class="v">${cd.commodity || ""}</div></div>
     <div class="row"><div class="k">variable</div><div class="v">${cd.via || ""}</div></div>
     <h3>Source node</h3>
     <div class="row"><div class="k">name</div><div class="v">${sNode.name || ""}</div></div>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[sNode.subtype] || sNode.subtype || ""}</div></div>
     <div class="row"><div class="k">PADD/state</div><div class="v">${(sNode.padd || "") + " " + (sNode.state ? "/ " + sNode.state : "")}</div></div>
     <div class="row"><div class="k">vars/TS</div><div class="v">${sNode.n_vars || 0} / ${sNode.n_ts || 0}</div></div>
     <h3>Target node</h3>
     <div class="row"><div class="k">name</div><div class="v">${tNode.name || ""}</div></div>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[tNode.subtype] || tNode.subtype || ""}</div></div>
     <div class="row"><div class="k">PADD/state</div><div class="v">${(tNode.padd || "") + " " + (tNode.state ? "/ " + tNode.state : "")}</div></div>
     <div class="row"><div class="k">vars/TS</div><div class="v">${tNode.n_vars || 0} / ${tNode.n_ts || 0}</div></div>`;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function fmtValue(v) {
  if (v === null || v === undefined) return null;
  const x = Number(v);
  if (!Number.isFinite(x)) return String(v);
  // Pick precision based on magnitude: small inventories are kbbl (large ints),
  // flows are kbd (smaller), latents come through as null
  if (Math.abs(x) >= 100000) return x.toFixed(0);
  if (Math.abs(x) >= 100)    return x.toFixed(0);
  if (Math.abs(x) >= 10)     return x.toFixed(1);
  return x.toFixed(2);
}

function renderVariables(vars) {
  if (!vars || vars.length === 0) return "";
  // Group by type, preserving conventional order
  const ORDER = ["production", "consumption", "inventory", "balancing_item", "inflow", "outflow", "phi"];
  const groups = {};
  for (const v of vars) {
    (groups[v.type] = groups[v.type] || []).push(v);
  }
  let html = `<h3>Variables (${vars.length})</h3>`;
  let snapshotDate = null;
  for (const t of ORDER) {
    const items = groups[t];
    if (!items) continue;
    const niceType = t.replace("_", " ");
    html += `<div class="vargroup">${niceType} (${items.length})</div>`;
    for (const v of items) {
      // Badge: TS / F / L (latent) / X (default-no-assignment)
      let badgeClass = "x", badgeText = "—";
      if (v.kind === "TS")                         { badgeClass = "ts"; badgeText = "TS"; }
      else if (v.detail && v.detail.includes("latent")) { badgeClass = "l";  badgeText = "L";  }
      else if (v.kind === "F")                     { badgeClass = "f";  badgeText = "F";  }
      // Name column: related node (if any) + detail (TS id or formula)
      let nameHtml = "";
      if (v.related) nameHtml += `<span class="arrow-rel">→</span> ${escapeHtml(v.related)}`;
      else           nameHtml += `<span style="color:#aaa">(this node)</span>`;
      if (v.detail) {
        const d = v.detail.length > 60 ? v.detail.substring(0, 57) + "…" : v.detail;
        nameHtml += `<span class="detail">${escapeHtml(d)}</span>`;
      }
      // Value column: last resolved value (formatted) or "—" if null/latent
      const valStr = fmtValue(v.value);
      const valHtml = valStr === null
        ? `<span class="val null">${v.source === "latent" ? "latent" : "—"}</span>`
        : `<span class="val">${valStr}</span>`;
      if (v.date && !snapshotDate) snapshotDate = v.date;
      const hasSeries = v.id && SERIES[v.id];
      const clickAttrs = hasSeries
        ? ` data-varid="${escapeHtml(v.id)}" onclick="onVarClick(this, '${escapeHtml(v.id)}')"`
        : ` class="var no-series"`;
      // If hasSeries we want the click handler AND the .var class; otherwise no-series
      if (hasSeries) {
        html += `<div class="var"${clickAttrs}>` +
                `<span class="badge ${badgeClass}">${badgeText}</span>` +
                `<span class="name">${nameHtml}</span>${valHtml}</div>`;
      } else {
        html += `<div${clickAttrs}>` +
                `<span class="badge ${badgeClass}">${badgeText}</span>` +
                `<span class="name">${nameHtml}</span>${valHtml}</div>`;
      }
    }
  }
  if (snapshotDate) {
    html += `<div class="vardate">values as of ${escapeHtml(snapshotDate)}</div>`;
  }
  return html;
}

function renderNode(asset_id) {
  const n = NODE_BY_ID[asset_id];
  if (!n) return;
  const side = document.getElementById("side");
  side.innerHTML =
    `<h3>Node</h3>
     <h2>${escapeHtml(n.asset_id)}</h2>
     <div class="row"><div class="k">name</div><div class="v">${escapeHtml(n.name || "")}</div></div>
     <div class="row"><div class="k">subtype</div><div class="v">${escapeHtml(SUBTYPE_LABEL[n.subtype] || n.subtype || "")}</div></div>
     <div class="row"><div class="k">PADD/state</div><div class="v">${escapeHtml((n.padd || "") + " " + (n.state ? "/ " + n.state : ""))}</div></div>
     <div class="row"><div class="k">coords</div><div class="v">${n.lat.toFixed(3)}, ${n.lon.toFixed(3)}${n.inferred ? " (inferred)" : ""}</div></div>
     <div class="row"><div class="k">variables</div><div class="v">${n.n_vars}</div></div>
     <div class="row"><div class="k">TS-bound</div><div class="v">${n.n_ts}</div></div>` +
     renderVariables(n.variables);
}

mapDiv.on("plotly_click", function(ev) {
  if (!ev.points || ev.points.length === 0) return;
  const cd = ev.points[0].customdata;
  if (!cd) return;
  if (cd.kind === "edge") renderEdge(cd);
  else if (cd.kind === "node") renderNode(cd.asset_id);
});

// ---- Variable click handler — plot time series in the third panel ----
function onVarClick(el, varId) {
  // toggle selected state
  document.querySelectorAll('#side .var.selected').forEach(e => e.classList.remove('selected'));
  el.classList.add('selected');
  renderPlot(varId);
}

function renderPlot(varId) {
  const s = SERIES[varId];
  const panel = document.getElementById('plot-panel');
  const plotDiv = document.getElementById('plot');
  const placeholder = panel.querySelector('.placeholder');

  if (!s || !DATES.length) {
    plotDiv.style.display = 'none';
    placeholder.style.display = 'block';
    placeholder.textContent = 'No time-series data for this variable.';
    return;
  }

  // Split into observed vs derived traces for visual distinction
  const observedX = [], observedY = [];
  const derivedX = [], derivedY = [];
  const otherX = [], otherY = [];
  for (let i = 0; i < DATES.length; i++) {
    const v = s.values[i];
    if (v === null || v === undefined) continue;
    const src = s.sources[i];
    if (src === 'observed') { observedX.push(DATES[i]); observedY.push(v); }
    else if (src === 'derived' || src === 'arithmetic' || src === 'sum' ||
             src === 'reverse_mirror' || src === 'alias') {
      derivedX.push(DATES[i]); derivedY.push(v);
    } else {
      otherX.push(DATES[i]); otherY.push(v);
    }
  }

  if (observedX.length + derivedX.length + otherX.length === 0) {
    plotDiv.style.display = 'none';
    placeholder.style.display = 'block';
    placeholder.textContent = 'This variable has no resolved values (likely all latent / null).';
    return;
  }

  // Build the title block (above the plot, inside panel)
  let titleHtml = `<h2>${varId}</h2>
                   <div class="sub">${observedX.length} observed &middot; ${derivedX.length} derived &middot; ${otherX.length} other</div>`;
  // Replace any existing title block above #plot
  const existingTitle = panel.querySelector('.title-block');
  if (existingTitle) existingTitle.remove();
  const titleBlock = document.createElement('div');
  titleBlock.className = 'title-block';
  titleBlock.innerHTML = titleHtml;
  panel.insertBefore(titleBlock, plotDiv);

  placeholder.style.display = 'none';
  plotDiv.style.display = 'block';

  const traces = [];
  if (observedX.length) traces.push({
    type: 'scatter', mode: 'lines', name: 'observed',
    x: observedX, y: observedY,
    line: { color: '#1f6feb', width: 2, shape: 'linear' },
  });
  if (derivedX.length) traces.push({
    type: 'scatter', mode: 'lines', name: 'derived',
    x: derivedX, y: derivedY,
    line: { color: '#888', width: 1.5, dash: 'dot' },
  });
  if (otherX.length) traces.push({
    type: 'scatter', mode: 'lines', name: 'other',
    x: otherX, y: otherY,
    line: { color: '#d97706', width: 1.2 },
  });

  Plotly.newPlot(plotDiv, traces, {
    margin: { l: 50, r: 12, t: 8, b: 32 },
    xaxis: { tickfont: { size: 10 } },
    yaxis: { tickfont: { size: 10 }, zeroline: true, zerolinecolor: '#ddd' },
    legend: { font: { size: 10 }, orientation: 'h', x: 0, y: 1.06 },
    showlegend: traces.length > 1,
    hovermode: 'x unified',
  }, { responsive: true, displaylogo: false });
}
</script>
</body>
</html>
"""


def main():
    print("Querying DB...")
    nodes, counts, edges = fetch()

    n_total = len(nodes)
    n_native = sum(1 for n in nodes if n.get("lat") is not None and not n.get("inferred_coords"))
    n_inferred = sum(1 for n in nodes if n.get("inferred_coords"))
    n_no_coords = sum(1 for n in nodes if n.get("lat") is None)
    print(f"  physical nodes: {n_total} total ({n_native} native coords, "
          f"{n_inferred} inferred from neighbours, {n_no_coords} unplottable)")
    print(f"  physical-to-physical edges: {len(edges)}")

    payload = build_payload(nodes, counts, edges)
    n_subtypes = len(payload["nodes_by_sub"])

    html = HTML_TEMPLATE
    html = html.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__SUBTYPE_COLOR__", json.dumps(SUBTYPE_COLOR))
    html = html.replace("__SUBTYPE_LABEL__", json.dumps(SUBTYPE_LABEL))
    html = html.replace("__N_NODES__", str(len(nodes)))
    html = html.replace("__N_EDGES__", str(len(edges)))
    html = html.replace("__N_SUBTYPES__", str(n_subtypes))

    HTML_OUT.write_text(html, encoding="utf-8")
    size_kb = HTML_OUT.stat().st_size // 1024
    print(f"Wrote {HTML_OUT}  ({size_kb} KB)")

    # Per-subtype counts for reference
    print("\nBy subtype:")
    for sub, ns in sorted(payload["nodes_by_sub"].items(), key=lambda x: -len(x[1])):
        print(f"  {SUBTYPE_LABEL.get(sub, sub):28s} {len(ns):4d}")


if __name__ == "__main__":
    main()
