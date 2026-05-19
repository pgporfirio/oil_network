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


def build_payload(nodes, counts, edges):
    by_id = {n["asset_id"]: n for n in nodes}

    # Group nodes by subtype for the trace structure (one trace per subtype = clean legend)
    nodes_by_sub = defaultdict(list)
    for n in nodes:
        if n.get("lat") is None or n.get("lon") is None:
            continue  # still uncoordinatable -- skip
        c = counts.get(n["asset_id"], {"n_vars": 0, "n_ts": 0})
        nodes_by_sub[n["node_subtype"]].append({
            "asset_id": n["asset_id"],
            "name":     n["name"],
            "lat":      n["lat"],
            "lon":      n["lon"],
            "padd":     n["padd"],
            "state":    n["state"],
            "n_vars":   c["n_vars"],
            "n_ts":     c["n_ts"],
            "inferred": bool(n.get("inferred_coords")),
        })

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
  .layout { display: grid; grid-template-columns: 1fr 320px; height: calc(100vh - 38px); }
  #map { width: 100%; height: 100%; }
  #side { background: #ffffff; border-left: 1px solid #d0d0c8;
          padding: 14px 16px; overflow-y: auto; font-size: 12.5px; }
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
</div>
<script>
const DATA = __DATA__;
const NODES_BY_SUB = DATA.nodes_by_sub;
const EDGES = DATA.edges;

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

function renderNode(asset_id) {
  const n = NODE_BY_ID[asset_id];
  if (!n) return;
  const side = document.getElementById("side");
  side.innerHTML =
    `<h3>Node</h3>
     <h2>${n.asset_id}</h2>
     <div class="row"><div class="k">name</div><div class="v">${n.name || ""}</div></div>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[n.subtype] || n.subtype || ""}</div></div>
     <div class="row"><div class="k">PADD/state</div><div class="v">${(n.padd || "") + " " + (n.state ? "/ " + n.state : "")}</div></div>
     <div class="row"><div class="k">coords</div><div class="v">${n.lat.toFixed(3)}, ${n.lon.toFixed(3)}${n.inferred ? " (inferred)" : ""}</div></div>
     <div class="row"><div class="k">variables</div><div class="v">${n.n_vars}</div></div>
     <div class="row"><div class="k">TS-bound</div><div class="v">${n.n_ts}</div></div>`;
}

mapDiv.on("plotly_click", function(ev) {
  if (!ev.points || ev.points.length === 0) return;
  const cd = ev.points[0].customdata;
  if (!cd) return;
  if (cd.kind === "edge") renderEdge(cd);
  else if (cd.kind === "node") renderNode(cd.asset_id);
});
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
