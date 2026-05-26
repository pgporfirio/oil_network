"""Generate a click-to-drill partition explorer on a geographic map.

Output: oil_network_partition_map.html

Click `usa_view` to expand its partition children (the 5 PADDs). Click a PADD
to expand its children (refining districts, basins, gathering nodes, hubs,
aggregates). Continue until you reach physical leaves. Click an expanded node
to collapse it back. "Reset" returns to the initial view.

Partition tree definition (matches `audit_partition_gaps.py`):
  child(N) := any node M such that some variable on N has formula_inputs
              referencing a variable on M, with same variable_type, same
              commodity, and same related_node_id. The same-type filter is
              what distinguishes aggregation (parent = Σ children) from
              cross-edge aliases (different variable types).

Coordinates:
  - Physical nodes: from oil_network.locations.lat/lon
  - Abstract aggregates: centroid of their physical descendants (computed
    here by transitive descent through the partition tree)
  - Pipelines / nodes without explicit coords: inferred from flow-edge
    neighbours (averaged), iterated until convergence
"""
from __future__ import annotations
from paths import HTML_DIR

import json
from collections import defaultdict
from pathlib import Path

from network_graph import NetworkGraph
from render_utils import latest_run_id, write_html
from variable_data import fetch_node_variables, fetch_series

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_partition_map.html"

SCENARIO = "starter_us_crude_2015_2025"
VIEW_NAME = "partition_map"

SUBTYPE_COLOR = {
    "state_conventional":    "#1f77b4",
    "state_sub_basin":       "#1f77b4",
    "state_residual":        "#1f77b4",
    "offshore_region":       "#1f77b4",
    "gathering":             "#aec7e8",
    "origin_terminal":       "#9467bd",
    "storage_terminal":      "#ff7f0e",
    "spr_site":              "#d62728",
    "pipeline":              "#7f7f7f",
    "refinery":              "#2ca02c",
    "import_terminal":       "#8c564b",
    "export_terminal":       "#e377c2",
    "foreign_export_destination": "#bcbd22",
    "foreign_production_aggregate": "#17becf",
    # Abstract subtypes
    "region_view":              "#0f1a4f",
    "observational_aggregate":  "#3f3f8f",
    "refining_district_view":   "#5f8f5f",
    "state_view":               "#3f5f8f",
    "usa_subtotal_view":        "#1a1f5f",
}
SUBTYPE_LABEL = {
    "state_conventional":           "Production (state)",
    "state_sub_basin":              "Production (sub-basin)",
    "state_residual":               "Production (residual)",
    "offshore_region":              "Production (offshore)",
    "gathering":                    "Gathering",
    "origin_terminal":              "Origin terminal",
    "storage_terminal":             "Storage hub",
    "spr_site":                     "SPR site",
    "pipeline":                     "Pipeline",
    "refinery":                     "Refinery",
    "import_terminal":              "Import terminal",
    "export_terminal":              "Export terminal",
    "foreign_export_destination":   "Foreign export sink",
    "foreign_production_aggregate": "Foreign supply",
    "region_view":                  "PADD / USA view",
    "observational_aggregate":      "Observational agg",
    "refining_district_view":       "Refining district",
    "state_view":                   "State view",
    "usa_subtotal_view":            "USA subtotal",
}


# Top-level partition roots: usa_view (the geographic partition).
ROOT_NODES = ["usa_view"]


def _infer_aggregate_coords(g: NetworkGraph):
    """Fill missing lat/lon on abstract aggregates by averaging the
    coordinates of their partition descendants.

    Physical nodes have explicit lat/lon (or are coords-inferred from flow
    neighbours upstream); observational aggregates (spr_total,
    usa_lower48_excl_gom_view, basin views, foreign aggregates) have neither
    locations nor flow neighbours, so they are skipped by the renderer
    unless we place them here. We place them at the centroid of their
    leaf descendants &mdash; the only sensible geographic anchor for a
    pure roll-up node.
    """
    # Seed only from physical nodes. The graph engine may infer coordinates
    # for abstract nodes from flow neighbours; using those here can pin
    # aggregates (including usa_view) to arbitrary flow-centric anchors
    # instead of partition centroids.
    coords = {
      nid: (n.lat, n.lon)
      for nid, n in g.nodes.items()
      if n.lat is not None and n.kind == "physical"
    }

    def descend_leaves(root, seen):
        """Return the lat/lon of every coords-carrying descendant of root
        via partition children. Cycle-safe (seen set)."""
        out = []
        stack = [root]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in coords:
                out.append(coords[cur])
            for c in g.partition_children(cur):
                stack.append(c)
        return out

    # Multiple passes: an aggregate's descendants might themselves be
    # aggregates that get coords in this loop. Three passes converges for
    # a graph of this depth.
    for _ in range(3):
        changed = False
        for nid, n in g.nodes.items():
            if nid in coords:
                continue
            # Pre-seeding `seen` with nid would cause descend_leaves to skip
            # the root on its first pop (the `if cur in seen: continue` guard
            # fires before children are pushed), leaving pts empty. Start the
            # visit set fresh — the in-loop `seen.add(cur)` handles cycle
            # avoidance correctly.
            pts = descend_leaves(nid, set())
            if pts:
                lat = sum(p[0] for p in pts) / len(pts)
                lon = sum(p[1] for p in pts) / len(pts)
                coords[nid] = (lat, lon)
                changed = True
        if not changed:
            break

    # Last-resort fallback: aggregates with no partition descendants (pure
    # TS-bound constraint nodes like spr_total, usa_lower48_excl_gom_view)
    # have no natural geographic anchor under the partition tree. Place
    # them at the centroid of their partition parents' OTHER children
    # &mdash; effectively saying "render this peer where its siblings sit".
    # Without this, such nodes are invisible on the map.
    for nid, n in g.nodes.items():
        if nid in coords:
            continue
        parents = g.partition_parents(nid)
        sibling_coords = []
        for p in parents:
            for sib in g.partition_children(p):
                if sib != nid and sib in coords:
                    sibling_coords.append(coords[sib])
        if sibling_coords:
            lat = sum(p[0] for p in sibling_coords) / len(sibling_coords)
            lon = sum(p[1] for p in sibling_coords) / len(sibling_coords)
            coords[nid] = (lat, lon)
    return coords


def build_payload(g: NetworkGraph):
    """Build the JSON payload consumed by the HTML/JS template.

    Everything comes from the NetworkGraph engine: nodes + metadata,
    partition children/parents. Coordinates for abstract aggregates are
    inferred here from their partition descendants (the engine carries
    raw lat/lon only for nodes with explicit locations).
    """
    inferred_coords = _infer_aggregate_coords(g)

    def display_children(parent_id: str) -> list[str]:
      """UI-level child filter for a clearer drill-down tree.

      The structural partition graph can include auxiliary aggregates directly
      under usa_view; for navigation we want the first click on usa_view to
      reveal only the five PADD region views.
      """
      kids = g.partition_children(parent_id)
      if parent_id == "usa_view":
        padd_kids = [
          c for c in kids
          if c in g.nodes and g.nodes[c].node_subtype == "region_view" and c != "usa_view"
        ]
        if padd_kids:
          return sorted(padd_kids)
      return kids

    nodes_data = {}
    for nid, n in g.nodes.items():
        lat_lon = inferred_coords.get(nid)
        if lat_lon is None:
            continue
        lat, lon = lat_lon
        n_children = len(display_children(nid))
        nodes_data[nid] = {
            "asset_id":   nid,
            "name":       n.name,
            "kind":       n.kind,
            "subtype":    n.node_subtype,
            "lat":        lat,
            "lon":        lon,
            "padd":       n.padd,
            "state":      n.state,
            "n_children": n_children,
            "is_root":    nid in ROOT_NODES,
            "coords_inferred": (n.lat is None),
        }

    # Build children once, AFTER nodes_data is fully populated. Previously
    # this lived inside the per-node loop, so it rebuilt on every iteration
    # and (worse) skipped entirely whenever the loop hit `continue` on a
    # coord-less node — leaving the dicts pinned to whichever node happened
    # to be iterated last.
    children = {}
    for p in nodes_data:
        kids = [c for c in display_children(p) if c in nodes_data]
        if kids:
            children[p] = kids

    parents_map = defaultdict(list)
    for p, kids in children.items():
        for c in kids:
            parents_map[c].append(p)
    parents = {c: sorted(ps) for c, ps in parents_map.items()}

    # Per-node variable details + per-variable time series (shared with the
    # map_resolver renderer via variable_data.py). Embedded here so the side
    # panel and plot panel work offline (no runtime DB query).
    node_vars = fetch_node_variables(SCENARIO)
    dates, series = fetch_series(SCENARIO)

    return {
        "nodes": nodes_data,
        "children": children,
        "parents":  parents,
        "roots":    ROOT_NODES,
        "node_vars": node_vars,
        "dates":    dates,
        "series":   series,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>oil_network &mdash; partition drill-down map</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { margin: 0; font: 13px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #f7f7f5; color: #222; }
  header { padding: 8px 16px; background: #1a1f2c; color: #fff;
           display: flex; gap: 12px; align-items: center; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; }
  header .stats { margin-left: auto; font-size: 11.5px; color: #ccc; }
  .controls { display: flex; gap: 8px; align-items: center; }
  .controls button { background: #2a3050; color: #fff; border: 1px solid #4a5070;
                     padding: 4px 10px; cursor: pointer; font: inherit; }
  .controls button:hover { background: #3a4070; }
  #breadcrumb { padding: 6px 16px; background: #ececea; border-bottom: 1px solid #d0d0c8;
                font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
  #breadcrumb .crumb { color: #1a4f8f; cursor: pointer; text-decoration: underline; }
  #breadcrumb .crumb:hover { color: #2a6faf; }
  #breadcrumb .sep { color: #888; padding: 0 4px; }
  .layout { display: grid; grid-template-columns: 1fr 320px 420px;
            height: calc(100vh - 38px - 30px); }
  #map { width: 100%; height: 100%; }
  #side { background: #fff; border-left: 1px solid #d0d0c8;
          padding: 14px 16px; overflow-y: auto; font-size: 12.5px; }
  #side h2 { margin: 0 0 6px 0; font-size: 14px;
             font-family: ui-monospace, Menlo, monospace; }
  #side h3 { margin: 12px 0 4px 0; font-size: 12px; color: #666;
             text-transform: uppercase; letter-spacing: 0.04em; }
  #side .row { display: grid; grid-template-columns: 90px 1fr; gap: 4px 8px; }
  #side .row .k { color: #777; }
  #side .row .v { font-family: ui-monospace, Menlo, monospace; word-break: break-all; }
  #side .placeholder { color: #999; padding: 60px 0; text-align: center; font-style: italic; }
  #side .child-list { font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; }
  #side .child-list a { color: #1a4f8f; cursor: pointer; }
  #side .hint { color: #666; font-size: 11.5px; padding: 8px; background: #f4f4f0;
                margin: 8px 0; border-radius: 3px; }
  /* Variable list (same look as map_resolver third panel) */
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
  #side .var .badge.l  { background: #d97706; }
  #side .var .badge.x  { background: #ccc; color: #555; }
  #side .var .name { color: #333; word-break: break-all; }
  #side .var .name .arrow-rel { color: #999; margin: 0 3px; }
  #side .var .name .detail { color: #888; font-size: 10px; display: block; margin-top: 1px; }
  #side .var .val { color: #111; font-weight: 600; white-space: nowrap; }
  #side .var .val.null { color: #d97706; font-weight: 400; font-style: italic; }
  #side .vardate { font-size: 10px; color: #999; margin-top: 4px; font-style: italic; }
  /* Third panel: time-series plot */
  #plot-panel { background: #fff; border-left: 1px solid #d0d0c8;
                padding: 12px 12px 8px; display: flex; flex-direction: column;
                font-size: 12.5px; min-width: 0; }
  #plot-panel .title-block h2 { margin: 0 0 4px 0; font-size: 13px;
                                font-family: ui-monospace, Menlo, monospace;
                                word-break: break-all; color: #222; }
  #plot-panel .title-block .sub { font-size: 11px; color: #777; margin-bottom: 8px; }
  #plot-panel .placeholder { color: #999; padding: 60px 12px; text-align: center;
                             font-style: italic; font-size: 12px; flex: 1; }
  #plot-panel #plot { flex: 1; min-height: 0; }
</style>
</head>
<body>
<header>
  <h1>oil_network &middot; partition drill-down</h1>
  <div class="controls">
    <button id="reset">Reset</button>
    <button id="expand-all">Expand all</button>
    <button id="focus">Focus on selected</button>
  </div>
  <div class="stats">
    <b>__N_NODES__</b> nodes &middot; <b>__N_EDGES__</b> partition edges
  </div>
</header>
<div id="breadcrumb"></div>
<div class="layout">
  <div id="map"></div>
  <aside id="side">
    <div class="placeholder">Click a node on the map to drill in.<br>Click a node again to collapse.</div>
  </aside>
  <aside id="plot-panel">
    <div class="placeholder">Click a variable in the middle panel to see its time series.</div>
    <div id="plot" style="display:none;"></div>
  </aside>
</div>
<script>
const DATA = __DATA__;
const SUBTYPE_COLOR = __SUBTYPE_COLOR__;
const SUBTYPE_LABEL = __SUBTYPE_LABEL__;
const NODES = DATA.nodes;
const CHILDREN = DATA.children;
const PARENTS  = DATA.parents;
const ROOTS    = DATA.roots;
const NODE_VARS = DATA.node_vars || {};   // node_id -> [variable details]
const DATES    = DATA.dates || [];        // shared x-axis for time-series plots
const SERIES   = DATA.series || {};       // variable_id -> {values, sources}

// expanded = set of node_ids whose children are currently visible.
// Initially: just the root. Clicking a node toggles its expansion.
let expanded = new Set(ROOTS);
let currentSelected = ROOTS[0];
// focusRoot = when set, only show this node and its descendants (the
// "focus mode" Pedro asked for: drill into a single subtree, hide the
// rest of the partition tree from view). null = normal mode.
let focusRoot = null;

function visibleNodes() {
  // Roots from which we expand: either the focusRoot if active,
  // otherwise all top-level ROOTS. In focus mode we also force the
  // focusRoot to be in `expanded` so its children are visible.
  const seeds = focusRoot ? [focusRoot] : ROOTS;
  if (focusRoot) expanded.add(focusRoot);
  const vis = new Set(seeds);
  const queue = [...seeds];
  while (queue.length) {
    const n = queue.shift();
    if (!expanded.has(n)) continue;
    for (const c of (CHILDREN[n] || [])) {
      if (!vis.has(c)) {
        vis.add(c);
        queue.push(c);
      }
    }
  }
  return vis;
}

function visibleEdges() {
  // An edge is shown if its parent is expanded AND both endpoints are
  // currently visible. In focus mode this naturally restricts edges to
  // the focused subtree; in normal mode it shows every materialised
  // parent->child link.
  const visN = visibleNodes();
  const edges = [];
  for (const p of expanded) {
    if (!visN.has(p)) continue;
    for (const c of (CHILDREN[p] || [])) {
      if (visN.has(c)) edges.push([p, c]);
    }
  }
  return edges;
}

function render() {
  const visN = visibleNodes();
  const edges = visibleEdges();

  // Build edge traces (lines + invisible midpoint markers for hover)
  const edgeLat = [], edgeLon = [], midLat = [], midLon = [], midCustom = [], midHover = [];
  for (const [p, c] of edges) {
    const pn = NODES[p], cn = NODES[c];
    if (!pn || !cn) continue;
    edgeLat.push(pn.lat, cn.lat, null);
    edgeLon.push(pn.lon, cn.lon, null);
    midLat.push((pn.lat + cn.lat) / 2);
    midLon.push((pn.lon + cn.lon) / 2);
    midCustom.push({kind: "edge", parent: p, child: c});
    midHover.push(`<b>${p} → ${c}</b><br><i>partition: parent → child</i>`);
  }

  const traces = [{
    type: "scattergeo", mode: "lines",
    lat: edgeLat, lon: edgeLon,
    line: { width: 1, color: "rgba(60,60,90,0.5)" },
    hoverinfo: "skip", name: "partition links",
  }, {
    type: "scattergeo", mode: "markers",
    lat: midLat, lon: midLon, hovertext: midHover, hoverinfo: "text",
    customdata: midCustom,
    marker: { size: 10, color: "rgba(0,0,0,0)", opacity: 0.001 },
    showlegend: false, hoverlabel: { bgcolor: "#fffdd0" },
  }];

  // Group visible nodes by subtype
  const bySub = {};
  for (const nid of visN) {
    const n = NODES[nid];
    if (!n) continue;
    (bySub[n.subtype] || (bySub[n.subtype] = [])).push(n);
  }
  for (const sub of Object.keys(SUBTYPE_COLOR)) {
    const ns = bySub[sub] || [];
    if (!ns.length) continue;
    traces.push({
      type: "scattergeo", mode: "markers",
      lat: ns.map(n => n.lat), lon: ns.map(n => n.lon),
      text: ns.map(n => {
        const arrow = expanded.has(n.asset_id) ? "▼" : (n.n_children > 0 ? "▶" : "●");
        return `<b>${n.asset_id}</b> ${arrow}<br>${n.name || ""}<br>` +
               `${SUBTYPE_LABEL[sub] || sub}<br>` +
               `${n.kind} &middot; ${n.n_children} children` +
               (n.n_children > 0 ? "<br><i>click to " + (expanded.has(n.asset_id) ? "collapse" : "expand") + "</i>"
                                  : "<br><i>leaf</i>");
      }),
      hoverinfo: "text",
      customdata: ns.map(n => ({kind: "node", asset_id: n.asset_id})),
      marker: {
        size: ns.map(n => n.kind === "physical" ? 8 :
                          n.asset_id === "usa_view" ? 22 :
                          n.subtype === "region_view" ? 16 : 12),
        color: SUBTYPE_COLOR[sub],
        line: { width: 1, color: "rgba(0,0,0,0.5)" },
        opacity: 0.92,
      },
      name: (SUBTYPE_LABEL[sub] || sub) + " (" + ns.length + ")",
    });
  }

  const layout = {
    margin: { l: 0, r: 0, t: 0, b: 0 },
    geo: {
      scope: "north america",
      projection: { type: "natural earth" },
      showland: true, landcolor: "#f4f1ed",
      countrycolor: "#999", showcoastlines: true, coastlinecolor: "#999",
      showsubunits: true, subunitcolor: "#ccc",
      lonaxis: { range: [-172, -55] }, lataxis: { range: [18, 75] },
      resolution: 50, showocean: true, oceancolor: "#e9eef3",
      showlakes: false,
    },
    legend: { bgcolor: "rgba(255,255,255,0.92)", bordercolor: "#ccc",
              borderwidth: 1, x: 0.005, y: 0.995, xanchor: "left", yanchor: "top",
              font: { size: 11 } },
  };

  const mapDiv = document.getElementById("map");
  Plotly.react(mapDiv, traces, layout, { responsive: true });
  mapDiv.removeAllListeners?.("plotly_click");
  mapDiv.on("plotly_click", function(ev) {
    if (!ev.points || ev.points.length === 0) return;
    const cd = ev.points[0].customdata;
    if (!cd) return;
    if (cd.kind === "node") onClickNode(cd.asset_id);
    else if (cd.kind === "edge") onClickEdge(cd);
  });
  renderBreadcrumb();
}

function onClickNode(asset_id) {
  currentSelected = asset_id;
  const n = NODES[asset_id];
  if (!n) return;
  // Toggle expansion if node has children
  if (n.n_children > 0) {
    if (expanded.has(asset_id)) {
      // Collapse: remove this node + recursively all its descendants from expanded
      const stack = [asset_id];
      while (stack.length) {
        const x = stack.pop();
        if (expanded.has(x)) {
          expanded.delete(x);
          stack.push(...(CHILDREN[x] || []));
        }
      }
    } else {
      expanded.add(asset_id);
    }
    render();
  } else {
    // Leaf — just update side panel
    renderSide(asset_id);
  }
  renderSide(asset_id);
}

function onClickEdge(cd) {
  const side = document.getElementById("side");
  side.innerHTML =
    `<h3>Partition edge</h3>
     <h2>${cd.parent} <span style="color:#888">⤇</span> ${cd.child}</h2>
     <div class="hint">Parent (${cd.parent}) aggregates child (${cd.child}) via formula_inputs (same variable_type).</div>
     <h3>Parent node</h3>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[NODES[cd.parent]?.subtype] || ""}</div></div>
     <div class="row"><div class="k">children</div><div class="v">${NODES[cd.parent]?.n_children || 0}</div></div>
     <h3>Child node</h3>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[NODES[cd.child]?.subtype] || ""}</div></div>
     <div class="row"><div class="k">grandchildren</div><div class="v">${NODES[cd.child]?.n_children || 0}</div></div>`;
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
  if (Math.abs(x) >= 100000) return x.toFixed(0);
  if (Math.abs(x) >= 100)    return x.toFixed(0);
  if (Math.abs(x) >= 10)     return x.toFixed(1);
  return x.toFixed(2);
}

function renderVariables(vars) {
  if (!vars || vars.length === 0) return "";
  const ORDER = ["production", "consumption", "inventory", "balancing_item", "inflow", "outflow", "phi"];
  const groups = {};
  for (const v of vars) (groups[v.type] = groups[v.type] || []).push(v);
  let html = `<h3>Variables (${vars.length})</h3>`;
  let snapshotDate = null;
  for (const t of ORDER) {
    const items = groups[t];
    if (!items) continue;
    html += `<div class="vargroup">${t.replace("_", " ")} (${items.length})</div>`;
    for (const v of items) {
      let badgeClass = "x", badgeText = "—";
      if (v.kind === "TS")                                  { badgeClass = "ts"; badgeText = "TS"; }
      else if (v.detail && v.detail.includes("latent"))     { badgeClass = "l";  badgeText = "L";  }
      else if (v.kind === "F")                              { badgeClass = "f";  badgeText = "F";  }
      let nameHtml = v.related
        ? `<span class="arrow-rel">→</span> ${escapeHtml(v.related)}`
        : `<span style="color:#aaa">(this node)</span>`;
      if (v.detail) {
        const d = v.detail.length > 60 ? v.detail.substring(0, 57) + "…" : v.detail;
        nameHtml += `<span class="detail">${escapeHtml(d)}</span>`;
      }
      const valStr = fmtValue(v.value);
      const valHtml = valStr === null
        ? `<span class="val null">${v.source === "latent" ? "latent" : "—"}</span>`
        : `<span class="val">${valStr}</span>`;
      if (v.date && !snapshotDate) snapshotDate = v.date;
      const hasSeries = v.id && SERIES[v.id];
      if (hasSeries) {
        html += `<div class="var" data-varid="${escapeHtml(v.id)}" onclick="onVarClick(this, '${escapeHtml(v.id)}')">` +
                `<span class="badge ${badgeClass}">${badgeText}</span>` +
                `<span class="name">${nameHtml}</span>${valHtml}</div>`;
      } else {
        html += `<div class="var no-series">` +
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

function renderSide(asset_id) {
  const n = NODES[asset_id];
  if (!n) return;
  const side = document.getElementById("side");
  const kids = CHILDREN[asset_id] || [];
  const ps = PARENTS[asset_id] || [];
  const vars = NODE_VARS[asset_id] || [];
  side.innerHTML =
    `<h3>Node</h3>
     <h2>${n.asset_id} ${expanded.has(asset_id) ? "▼" : (n.n_children > 0 ? "▶" : "●")}</h2>
     <div class="row"><div class="k">name</div><div class="v">${n.name || ""}</div></div>
     <div class="row"><div class="k">kind</div><div class="v">${n.kind}</div></div>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[n.subtype] || n.subtype || ""}</div></div>
     <div class="row"><div class="k">PADD/state</div><div class="v">${(n.padd || "") + " " + (n.state ? "/ " + n.state : "")}</div></div>
     <div class="row"><div class="k">coords</div><div class="v">${n.lat.toFixed(2)}, ${n.lon.toFixed(2)}</div></div>
     ${renderVariables(vars)}
     ${n.n_children > 0
       ? `<h3>Partition children (${kids.length})</h3>
          <div class="child-list">${kids.map(k => `<a onclick="onClickNode('${k}')">${k}</a>`).join("<br>")}</div>
          <div class="hint">Click on the map (or here) to ${expanded.has(asset_id) ? "collapse" : "expand"}.</div>`
       : `<div class="hint">Physical leaf — no partition children.</div>`}
     ${ps.length > 0
       ? `<h3>Partition parents</h3>
          <div class="child-list">${ps.map(p => `<a onclick="onClickNode('${p}')">${p}</a>`).join("<br>")}</div>`
       : ""}`;
}

// ---- Variable click handler — plot time series in the third panel ----
function onVarClick(el, varId) {
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

  const existingTitle = panel.querySelector('.title-block');
  if (existingTitle) existingTitle.remove();
  const titleBlock = document.createElement('div');
  titleBlock.className = 'title-block';
  titleBlock.innerHTML = `<h2>${escapeHtml(varId)}</h2>
    <div class="sub">${observedX.length} observed &middot; ${derivedX.length} derived &middot; ${otherX.length} other</div>`;
  panel.insertBefore(titleBlock, plotDiv);

  placeholder.style.display = 'none';
  plotDiv.style.display = 'block';

  const traces = [];
  if (observedX.length) traces.push({
    type: 'scatter', mode: 'lines', name: 'observed',
    x: observedX, y: observedY,
    line: { color: '#1f6feb', width: 2 },
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

function renderBreadcrumb() {
  // Trace from currentSelected up to root via PARENTS
  const path = [];
  let cur = currentSelected;
  let safety = 50;
  while (cur && safety-- > 0) {
    path.unshift(cur);
    const ps = PARENTS[cur] || [];
    cur = ps[0];  // pick first parent if multi-parent
    if (path.includes(cur)) break;
  }
  const bc = document.getElementById("breadcrumb");
  const focusBadge = focusRoot
    ? `<span class="sep">&middot;</span><span style="color:#a04020;font-weight:600">focus: ${focusRoot}</span>`
    : "";
  bc.innerHTML = path.map(p =>
    `<span class="crumb" onclick="onClickNode('${p}')">${p}</span>`
  ).join(`<span class="sep">▸</span>`) + focusBadge;
}

document.getElementById("reset").onclick = () => {
  expanded = new Set(ROOTS);
  currentSelected = ROOTS[0];
  focusRoot = null;
  document.getElementById("focus").textContent = "Focus on selected";
  render();
  renderSide(currentSelected);
};
document.getElementById("expand-all").onclick = () => {
  // Expand every non-leaf node (within the active focusRoot if any)
  for (const nid in NODES) {
    if ((CHILDREN[nid] || []).length > 0) expanded.add(nid);
  }
  render();
};
document.getElementById("focus").onclick = () => {
  // Toggle focus mode: enter focuses on currentSelected (if it has
  // children or is the root); leave returns to normal view.
  const btn = document.getElementById("focus");
  if (focusRoot) {
    focusRoot = null;
    btn.textContent = "Focus on selected";
  } else if (currentSelected && (CHILDREN[currentSelected] || []).length > 0) {
    focusRoot = currentSelected;
    expanded.add(focusRoot);
    btn.textContent = "Leave focus";
  }
  render();
  if (currentSelected) renderSide(currentSelected);
};

render();
renderSide(currentSelected);
</script>
</body>
</html>
"""


def main(g: NetworkGraph = None):
    """Generate the partition-drilldown map HTML.

    Accepts an existing NetworkGraph (so an orchestrator can build once and
    pass it to multiple renderers) or constructs one if not given.
    """
    if g is None:
        g = NetworkGraph(SCENARIO)
    print(f"Using {g}")

    payload = build_payload(g)
    n_partition = sum(len(g.partition_children(p)) for p in payload["nodes"])
    print(f"  payload nodes: {len(payload['nodes'])}  partition edges: {n_partition}")

    html = HTML_TEMPLATE
    html = html.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__SUBTYPE_COLOR__", json.dumps(SUBTYPE_COLOR))
    html = html.replace("__SUBTYPE_LABEL__", json.dumps(SUBTYPE_LABEL))
    html = html.replace("__N_NODES__", str(len(payload["nodes"])))
    html = html.replace("__N_EDGES__", str(n_partition))

    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
                              notes=f"partition map, {len(payload['nodes'])} nodes")
    size_kb = HTML_OUT.stat().st_size // 1024
    print(f"Wrote {HTML_OUT}  ({size_kb} KB)  [artefact_id={artefact_id}, run_id={run_id}]")


if __name__ == "__main__":
    main()
