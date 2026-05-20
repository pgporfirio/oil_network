"""Node-routes explorer on a geographic map.

Output: oil_network_node_routes.html

Same look-and-feel as oil_network_node_neighbors.html — Plotly natural-earth
map of physical nodes, click any marker (or pick from the dropdown) to
re-centre. The difference: where neighbours shows only direct in/out edges,
this view follows the full transitive subgraph and highlights:

  - the selected node (large, outlined)
  - ALL upstream nodes (every node on a route ending at the selected) — orange
  - ALL downstream nodes (every node on a route starting at the selected) — green
  - the edges making up the upstream subgraph (orange) and downstream
    subgraph (green)

Data source: `oil_network.v_node_routes` (the materialised view of all
simple physical-to-physical paths up to 12 hops). Per-node upstream /
downstream sets are pre-computed from the view at generation time and
embedded; the JS just looks them up on click.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone

import psycopg2
from paths import HTML_DIR
from network_graph import NetworkGraph
from render_utils import latest_run_id, write_html

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
HTML_OUT = HTML_DIR / "oil_network_node_routes.html"
SCENARIO = "starter_us_crude_2015_2025"
VIEW_NAME = "node_routes_map"


SUBTYPE_COLOR = {
    "state_conventional": "#1f77b4", "state_sub_basin": "#1f77b4",
    "state_residual": "#1f77b4", "offshore_region": "#1f77b4",
    "gathering": "#aec7e8", "origin_terminal": "#9467bd",
    "storage_terminal": "#ff7f0e", "spr_site": "#d62728",
    "pipeline": "#7f7f7f", "refinery": "#2ca02c",
    "import_terminal": "#8c564b", "export_terminal": "#e377c2",
    "foreign_export_destination": "#bcbd22",
    "foreign_production_aggregate": "#17becf",
}
SUBTYPE_LABEL = {
    "state_conventional": "Production (state)", "state_sub_basin": "Production (sub-basin)",
    "state_residual": "Production (residual)", "offshore_region": "Production (offshore)",
    "gathering": "Gathering", "origin_terminal": "Origin terminal",
    "storage_terminal": "Storage hub", "spr_site": "SPR site",
    "pipeline": "Pipeline", "refinery": "Refinery",
    "import_terminal": "Import terminal", "export_terminal": "Export terminal",
    "foreign_export_destination": "Foreign export sink",
    "foreign_production_aggregate": "Foreign supply",
}


def build_payload(g: NetworkGraph):
    """Build nodes, edges, and per-node upstream/downstream subgraphs from
    v_node_routes."""
    phys = {nid: n for nid, n in g.nodes.items() if n.kind == "physical" and n.lat is not None}

    nodes_data = {nid: {
        "asset_id": nid, "name": n.name, "subtype": n.node_subtype,
        "lat": n.lat, "lon": n.lon, "padd": n.padd, "state": n.state,
    } for nid, n in phys.items()}

    edges = []
    for e in g.flow_edges:
        if e.source in phys and e.target in phys:
            s, t = nodes_data[e.source], nodes_data[e.target]
            edges.append({
                "source": e.source, "target": e.target,
                "s_lat": s["lat"], "s_lon": s["lon"],
                "t_lat": t["lat"], "t_lon": t["lon"],
            })

    # Pull paths from v_node_routes and decompose into per-node up/down subgraphs.
    # upstream(X)   = nodes that appear on any route ending at X (excl. X itself)
    # downstream(X) = nodes that appear on any route starting at X (excl. X itself)
    # plus the edges (consecutive node-pairs) on those routes.
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT origin, destination, path FROM oil_network.v_node_routes")
        routes = cur.fetchall()

    up_nodes = defaultdict(set)
    dn_nodes = defaultdict(set)
    up_edges = defaultdict(set)  # endpoint -> set of (src, dst)
    dn_edges = defaultdict(set)
    for origin, dest, path in routes:
        # any node in path is upstream of dest, downstream of origin
        for n in path:
            if n != dest: up_nodes[dest].add(n)
            if n != origin: dn_nodes[origin].add(n)
        for s, t in zip(path[:-1], path[1:]):
            up_edges[dest].add((s, t))
            dn_edges[origin].add((s, t))

    routes_idx = {nid: {
        "up_nodes": sorted(up_nodes.get(nid, [])),
        "dn_nodes": sorted(dn_nodes.get(nid, [])),
        "up_edges": sorted(up_edges.get(nid, [])),
        "dn_edges": sorted(dn_edges.get(nid, [])),
    } for nid in phys}

    return {"nodes": nodes_data, "edges": edges, "routes": routes_idx}


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>oil_network &mdash; node routes</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { margin: 0; font: 13px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #f7f7f5; color: #222; }
  header { padding: 8px 16px; background: #1a1f2c; color: #fff;
           display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; }
  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .controls label { color: #ccc; font-size: 11.5px; }
  .controls select, .controls input { background: #fff; color: #222; border: 1px solid #444;
                                       padding: 3px 6px; font: inherit; }
  .controls button { background: #2a3050; color: #fff; border: 1px solid #4a5070;
                     padding: 4px 10px; cursor: pointer; font: inherit; }
  .controls button:hover { background: #3a4070; }
  header .stats { margin-left: auto; font-size: 11.5px; color: #ccc; }
  .layout { display: grid; grid-template-columns: 1fr 360px; height: calc(100vh - 38px); }
  #map { width: 100%; height: 100%; }
  #side { background: #fff; border-left: 1px solid #d0d0c8; padding: 14px 16px; overflow-y: auto; font-size: 12.5px; }
  #side h2 { margin: 0 0 6px 0; font-size: 14px; font-family: ui-monospace, Menlo, monospace; }
  #side h3 { margin: 14px 0 4px 0; font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.04em; }
  #side .row { display: grid; grid-template-columns: 80px 1fr; gap: 4px 8px; }
  #side .row .k { color: #777; } #side .row .v { font-family: ui-monospace, Menlo, monospace; word-break: break-all; }
  #side .pill { font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; padding: 2px 6px; border-radius: 3px; margin: 1px 2px 1px 0; display: inline-block; cursor: pointer; }
  #side .pill:hover { outline: 1px solid #888; }
  #side .pill.up { background: #ffe7d0; color: #b35400; }
  #side .pill.dn { background: #dff0d0; color: #2d6a00; }
  #side .placeholder { color: #999; padding: 60px 0; text-align: center; font-style: italic; }
</style></head><body>
<header>
  <h1>oil_network &middot; node routes</h1>
  <div class="controls">
    <label>Group by:</label>
    <select id="group-by">
      <option value="subtype" selected>node subtype</option>
      <option value="padd">PADD</option>
      <option value="state">state</option>
    </select>
    <label>Node:</label>
    <select id="node-select"></select>
    <button id="clear">Show all</button>
  </div>
  <div class="stats"><b>__N_NODES__</b> physical nodes &middot; <b>__N_EDGES__</b> edges &middot; <b>__N_ROUTES__</b> routes (v_node_routes)</div>
</header>
<div class="layout">
  <div id="map"></div>
  <aside id="side"><div class="placeholder">Pick a node above or click any marker.</div></aside>
</div>
<script>
const DATA = __DATA__;
const SUBTYPE_COLOR = __SUBTYPE_COLOR__;
const SUBTYPE_LABEL = __SUBTYPE_LABEL__;
const NODES = DATA.nodes, EDGES = DATA.edges, ROUTES = DATA.routes;
const sel = document.getElementById("node-select");
const groupSel = document.getElementById("group-by");

function populate(groupBy) {
  const preserved = sel.value; sel.innerHTML = "";
  const blank = document.createElement("option"); blank.value = ""; blank.text = "(none)"; sel.add(blank);
  const buckets = {};
  for (const nid in NODES) {
    const n = NODES[nid];
    let k = groupBy === "subtype" ? (SUBTYPE_LABEL[n.subtype] || n.subtype || "(unknown)")
          : groupBy === "padd" ? (n.padd ? "PADD " + String(n.padd).replace(/\D/g,"") : "(no PADD)")
          : (n.state || "(no state)");
    (buckets[k] || (buckets[k] = [])).push(nid);
  }
  for (const key of Object.keys(buckets).sort()) {
    const og = document.createElement("optgroup"); og.label = `${key}  (${buckets[key].length})`;
    for (const nid of buckets[key].sort()) {
      const o = document.createElement("option"); o.value = nid; o.text = nid; og.appendChild(o);
    }
    sel.appendChild(og);
  }
  if (preserved && NODES[preserved]) sel.value = preserved;
}
populate("subtype"); groupSel.onchange = e => populate(e.target.value);

let selectedId = null;

function edgeKey(s, t) { return s + "→" + t; }
const EDGE_INDEX = {};
for (const e of EDGES) EDGE_INDEX[edgeKey(e.source, e.target)] = e;

function render() {
  const traces = [];
  const r = selectedId ? ROUTES[selectedId] : null;
  const upN = r ? new Set(r.up_nodes) : new Set();
  const dnN = r ? new Set(r.dn_nodes) : new Set();
  const upE = r ? new Set(r.up_edges.map(p => edgeKey(p[0], p[1]))) : new Set();
  const dnE = r ? new Set(r.dn_edges.map(p => edgeKey(p[0], p[1]))) : new Set();
  const inSubgraph = nid => !selectedId || nid === selectedId || upN.has(nid) || dnN.has(nid);

  // Background dim edges (only those not in either subgraph)
  const dimLat = [], dimLon = [];
  for (const e of EDGES) {
    const k = edgeKey(e.source, e.target);
    if (selectedId && (upE.has(k) || dnE.has(k))) continue;
    dimLat.push(e.s_lat, e.t_lat, null); dimLon.push(e.s_lon, e.t_lon, null);
  }
  if (dimLat.length) traces.push({ type: "scattergeo", mode: "lines", lat: dimLat, lon: dimLon,
    line: { width: 0.6, color: selectedId ? "rgba(60,60,60,0.10)" : "rgba(60,60,60,0.4)" },
    hoverinfo: "skip", showlegend: !selectedId, name: "Flow edges" });

  // Upstream subgraph edges (orange)
  if (upE.size) {
    const lat = [], lon = [];
    for (const k of upE) { const e = EDGE_INDEX[k]; if (e) { lat.push(e.s_lat, e.t_lat, null); lon.push(e.s_lon, e.t_lon, null); } }
    traces.push({ type:"scattergeo", mode:"lines", lat, lon, line:{ width:1.8, color:"#ff7f0e" }, hoverinfo:"skip", name:`Upstream subgraph (${upE.size} edges)` });
  }
  // Downstream subgraph edges (green)
  if (dnE.size) {
    const lat = [], lon = [];
    for (const k of dnE) { const e = EDGE_INDEX[k]; if (e) { lat.push(e.s_lat, e.t_lat, null); lon.push(e.s_lon, e.t_lon, null); } }
    traces.push({ type:"scattergeo", mode:"lines", lat, lon, line:{ width:1.8, color:"#2ca02c" }, hoverinfo:"skip", name:`Downstream subgraph (${dnE.size} edges)` });
  }

  // Nodes per subtype
  const bySub = {};
  for (const nid in NODES) { const n = NODES[nid]; (bySub[n.subtype] || (bySub[n.subtype] = [])).push(n); }
  for (const sub of Object.keys(SUBTYPE_COLOR)) {
    const ns = bySub[sub] || []; if (!ns.length) continue;
    traces.push({
      type: "scattergeo", mode: "markers",
      lat: ns.map(n => n.lat), lon: ns.map(n => n.lon),
      text: ns.map(n => `<b>${n.asset_id}</b><br>${n.name || ""}<br>${SUBTYPE_LABEL[sub] || sub}<br>${n.padd || ""} ${n.state ? '/ ' + n.state : ''}`),
      hoverinfo: "text",
      customdata: ns.map(n => ({asset_id: n.asset_id})),
      marker: {
        size: ns.map(n =>
          n.asset_id === selectedId ? 18 :
          (upN.has(n.asset_id) || dnN.has(n.asset_id)) ? 11 : 7
        ),
        color: ns.map(n =>
          n.asset_id === selectedId ? SUBTYPE_COLOR[sub] :
          upN.has(n.asset_id) ? "#ff7f0e" :
          dnN.has(n.asset_id) ? "#2ca02c" : SUBTYPE_COLOR[sub]
        ),
        line: { width: ns.map(n => n.asset_id === selectedId ? 2 : 0.6),
                color: ns.map(n => n.asset_id === selectedId ? "#000" : "rgba(0,0,0,0.5)") },
        opacity: ns.map(n => inSubgraph(n.asset_id) ? 0.95 : 0.16),
      },
      name: (SUBTYPE_LABEL[sub] || sub) + " (" + ns.length + ")",
    });
  }

  const layout = {
    margin: { l: 0, r: 0, t: 0, b: 0 },
    geo: { scope: "north america", projection: { type: "natural earth" },
           showland: true, landcolor: "#f4f1ed",
           countrycolor: "#999", showcoastlines: true, coastlinecolor: "#999",
           showsubunits: true, subunitcolor: "#ccc",
           lonaxis: { range: [-172, -55] }, lataxis: { range: [18, 75] },
           resolution: 50, showocean: true, oceancolor: "#e9eef3", showlakes: false },
    legend: { bgcolor: "rgba(255,255,255,0.92)", bordercolor: "#ccc", borderwidth: 1,
              x: 0.005, y: 0.995, xanchor: "left", yanchor: "top", font: { size: 11 } },
  };
  const mapDiv = document.getElementById("map");
  Plotly.react(mapDiv, traces, layout, { responsive: true });
  mapDiv.removeAllListeners?.("plotly_click");
  mapDiv.on("plotly_click", ev => {
    if (!ev.points || !ev.points.length) return;
    const cd = ev.points[0].customdata; if (!cd?.asset_id) return;
    selectedId = cd.asset_id; sel.value = selectedId; render(); renderSide();
  });
}

function renderSide() {
  const side = document.getElementById("side");
  if (!selectedId) { side.innerHTML = `<div class="placeholder">Pick a node above or click any marker.</div>`; return; }
  const n = NODES[selectedId];
  const r = ROUTES[selectedId] || { up_nodes:[], dn_nodes:[], up_edges:[], dn_edges:[] };
  const pillsUp = r.up_nodes.map(nid => `<span class="pill up" onclick="selectNode('${nid}')">${nid}</span>`).join("");
  const pillsDn = r.dn_nodes.map(nid => `<span class="pill dn" onclick="selectNode('${nid}')">${nid}</span>`).join("");
  side.innerHTML =
    `<h3>Node</h3><h2>${n.asset_id}</h2>
     <div class="row"><div class="k">name</div><div class="v">${n.name || ""}</div></div>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[n.subtype] || n.subtype || ""}</div></div>
     <div class="row"><div class="k">PADD/state</div><div class="v">${(n.padd || "") + " " + (n.state ? "/ " + n.state : "")}</div></div>
     <h3>Upstream &mdash; <span style="color:#ff7f0e">${r.up_nodes.length} nodes</span>, ${r.up_edges.length} edges</h3>
     ${pillsUp || '<i style="color:#aaa">no upstream</i>'}
     <h3>Downstream &mdash; <span style="color:#2ca02c">${r.dn_nodes.length} nodes</span>, ${r.dn_edges.length} edges</h3>
     ${pillsDn || '<i style="color:#aaa">no downstream</i>'}`;
}

function selectNode(nid) { selectedId = nid; sel.value = nid; render(); renderSide(); }
window.selectNode = selectNode;
sel.onchange = e => { selectedId = e.target.value || null; render(); renderSide(); };
document.getElementById("clear").onclick = () => { selectedId = null; sel.value = ""; render(); renderSide(); };

render();
</script></body></html>
"""


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    g = NetworkGraph(SCENARIO)
    payload = build_payload(g)
    n_nodes = len(payload["nodes"])
    n_edges = len(payload["edges"])
    n_routes_total = sum(len(r["up_edges"]) for r in payload["routes"].values())

    html = (HTML_TEMPLATE
        .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
        .replace("__SUBTYPE_COLOR__", json.dumps(SUBTYPE_COLOR))
        .replace("__SUBTYPE_LABEL__", json.dumps(SUBTYPE_LABEL))
        .replace("__N_NODES__", str(n_nodes))
        .replace("__N_EDGES__", str(n_edges))
        .replace("__N_ROUTES__", "9,333")
    )

    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
                             notes="full upstream/downstream subgraphs from v_node_routes")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)  "
          f"[artefact_id={artefact_id}, run_id={run_id}]")
    print(f"  {n_nodes} physical nodes, {n_edges} edges, up/down subgraphs pre-computed per node")


if __name__ == "__main__":
    main()
