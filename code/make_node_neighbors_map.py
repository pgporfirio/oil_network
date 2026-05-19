"""Single-node neighbour explorer on a geographic map.

Output: oil_network_node_neighbors.html

Pick a physical node from the dropdown (or click any node on the map). The
view re-centres on that node, dims everything else, and highlights:

  - the selected node (large, outlined)
  - direct INFLOW neighbours (other end of each `inflow ← X` edge — orange)
  - direct OUTFLOW neighbours (other end of each `outflow → X` edge — green)
  - the corresponding edges, in the same colour

Edge labels (in the side panel) show the resolved value at the chosen
observation date, when available.

Restricted to physical-to-physical edges per the user request — abstract
aggregates and view nodes are not shown here. Use
`oil_network_partition_map.html` for partition-tree exploration.
"""
from __future__ import annotations
from paths import HTML_DIR

import json
from collections import defaultdict
from pathlib import Path

from network_graph import NetworkGraph
from render_utils import latest_run_id, write_html

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_node_neighbors.html"

SCENARIO = "starter_us_crude_2015_2025"
VIEW_NAME = "node_neighbors_map"
DEFAULT_DATE = "2024-12-01"


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


def build_payload(g: NetworkGraph):
    """Build the JSON payload directly from the NetworkGraph engine.

    Restricts to physical-to-physical edges per the renderer's contract.
    Edge values are looked up at DEFAULT_DATE.
    """
    # Physical nodes only — abstract aggregates live in the partition map view
    phys_ids = {nid for nid, n in g.nodes.items() if n.kind == "physical"}

    nodes_data = {}
    for nid in phys_ids:
        n = g.nodes[nid]
        if n.lat is None:
            continue
        nodes_data[nid] = {
            "asset_id": nid,
            "name":     n.name,
            "subtype":  n.node_subtype,
            "lat":      n.lat, "lon": n.lon,
            "padd":     n.padd, "state": n.state,
            "inferred": False,  # engine doesn't currently distinguish; cosmetic only
        }

    # Edges: physical -> physical only, with values at DEFAULT_DATE
    enriched_edges = []
    for e in g.flow_edges:
        if e.source not in phys_ids or e.target not in phys_ids:
            continue
        s = nodes_data.get(e.source)
        t = nodes_data.get(e.target)
        if not s or not t:
            continue
        val = g.value(e.via_variable, DEFAULT_DATE)
        enriched_edges.append({
            "source": e.source, "target": e.target,
            "via":    e.via_variable,
            "commodity": e.commodity,
            "value":  val, "src": "observed" if val is not None else "latent",
            "s_lat":  s["lat"], "s_lon": s["lon"],
            "t_lat":  t["lat"], "t_lon": t["lon"],
        })

    # Adjacency lookups for the side panel + highlighting
    inflow_to = defaultdict(list)
    outflow_from = defaultdict(list)
    for e in enriched_edges:
        inflow_to[e["target"]].append(e)
        outflow_from[e["source"]].append(e)

    return {
        "nodes": nodes_data,
        "edges": enriched_edges,
        "inflows_to":    dict(inflow_to),
        "outflows_from": dict(outflow_from),
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>oil_network &mdash; node-neighbour explorer</title>
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
  .layout { display: grid; grid-template-columns: 1fr 340px; height: calc(100vh - 38px); }
  #map { width: 100%; height: 100%; }
  #side { background: #fff; border-left: 1px solid #d0d0c8;
          padding: 14px 16px; overflow-y: auto; font-size: 12.5px; }
  #side h2 { margin: 0 0 6px 0; font-size: 14px;
             font-family: ui-monospace, Menlo, monospace; }
  #side h3 { margin: 14px 0 4px 0; font-size: 12px; color: #666;
             text-transform: uppercase; letter-spacing: 0.04em; }
  #side .row { display: grid; grid-template-columns: 80px 1fr; gap: 4px 8px; }
  #side .row .k { color: #777; }
  #side .row .v { font-family: ui-monospace, Menlo, monospace; word-break: break-all; }
  #side .edge { font-family: ui-monospace, Menlo, monospace; font-size: 11.5px;
                padding: 4px 6px; border-radius: 3px; margin-bottom: 3px; cursor: pointer; }
  #side .edge:hover { background: #f4f4f0; }
  #side .edge.in  { border-left: 3px solid #ff7f0e; padding-left: 8px; }
  #side .edge.out { border-left: 3px solid #2ca02c; padding-left: 8px; }
  #side .edge .val { float: right; color: #555; font-weight: 600; }
  #side .edge .latent { color: #aaa; font-style: italic; }
  #side .placeholder { color: #999; padding: 60px 0; text-align: center; font-style: italic; }
</style>
</head>
<body>
<header>
  <h1>oil_network &middot; node neighbours</h1>
  <div class="controls">
    <label>Group by:</label>
    <select id="group-by">
      <option value="subtype" selected>node subtype</option>
      <option value="padd">PADD</option>
      <option value="state">state</option>
    </select>
    <label>Node:</label>
    <select id="node-select"></select>
    <label>Date:</label>
    <input id="date" value="__DEFAULT_DATE__" readonly>
    <button id="clear">Show all</button>
  </div>
  <div class="stats">
    <b>__N_NODES__</b> physical nodes &middot; <b>__N_EDGES__</b> edges
  </div>
</header>
<div class="layout">
  <div id="map"></div>
  <aside id="side">
    <div class="placeholder">Pick a node above or click any marker.</div>
  </aside>
</div>
<script>
const DATA = __DATA__;
const SUBTYPE_COLOR = __SUBTYPE_COLOR__;
const SUBTYPE_LABEL = __SUBTYPE_LABEL__;
const NODES = DATA.nodes;
const EDGES = DATA.edges;
const INFLOWS_TO    = DATA.inflows_to;
const OUTFLOWS_FROM = DATA.outflows_from;

// Populate dropdown with <optgroup>s based on the active grouping.
// Three groupings are supported: by node_subtype, by PADD, by state.
const sel = document.getElementById("node-select");
const groupSel = document.getElementById("group-by");

function populateNodeSelect(groupBy) {
  const preserved = sel.value;
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = ""; blank.text = "(none)";
  sel.add(blank);

  const buckets = {};
  for (const nid of Object.keys(NODES)) {
    const n = NODES[nid];
    let key;
    if (groupBy === "subtype") {
      key = SUBTYPE_LABEL[n.subtype] || n.subtype || "(unknown subtype)";
    } else if (groupBy === "padd") {
      key = n.padd ? ("PADD " + String(n.padd).replace(/\D/g, "")) : "(no PADD)";
    } else if (groupBy === "state") {
      key = n.state || "(no state)";
    }
    (buckets[key] || (buckets[key] = [])).push(nid);
  }

  const keys = Object.keys(buckets).sort();
  for (const key of keys) {
    const og = document.createElement("optgroup");
    og.label = `${key}  (${buckets[key].length})`;
    for (const nid of buckets[key].sort()) {
      const o = document.createElement("option");
      o.value = nid;
      o.text = nid;
      og.appendChild(o);
    }
    sel.appendChild(og);
  }

  if (preserved && NODES[preserved]) {
    sel.value = preserved;
  }
}

populateNodeSelect("subtype");
groupSel.onchange = (e) => populateNodeSelect(e.target.value);

let selectedId = null;

function fmt(v) {
  if (v === null || v === undefined) return "<span class=latent>latent</span>";
  return Math.abs(v) < 0.01 ? "0" : v.toFixed(1) + " kbd";
}

function render() {
  const traces = [];

  // Determine which edges/nodes to highlight
  const edgesIn  = selectedId ? (INFLOWS_TO[selectedId] || []) : [];
  const edgesOut = selectedId ? (OUTFLOWS_FROM[selectedId] || []) : [];
  const isHi = (nid) => selectedId === null || nid === selectedId ||
                       edgesIn.some(e => e.source === nid) ||
                       edgesOut.some(e => e.target === nid);

  // All edges as dim grey lines (background)
  const dimLat = [], dimLon = [];
  for (const e of EDGES) {
    if (selectedId && (edgesIn.includes(e) || edgesOut.includes(e))) continue;
    dimLat.push(e.s_lat, e.t_lat, null);
    dimLon.push(e.s_lon, e.t_lon, null);
  }
  if (dimLat.length) {
    traces.push({
      type: "scattergeo", mode: "lines",
      lat: dimLat, lon: dimLon,
      line: { width: 0.6, color: selectedId ? "rgba(60,60,60,0.12)" : "rgba(60,60,60,0.4)" },
      hoverinfo: "skip", showlegend: !selectedId,
      name: "Flow edges",
    });
  }

  // Highlighted inflow edges (orange)
  if (edgesIn.length) {
    const lat = [], lon = [];
    for (const e of edgesIn) { lat.push(e.s_lat, e.t_lat, null); lon.push(e.s_lon, e.t_lon, null); }
    traces.push({
      type: "scattergeo", mode: "lines",
      lat, lon, line: { width: 2.2, color: "#ff7f0e" },
      hoverinfo: "skip", name: `Inflows (${edgesIn.length})`,
    });
  }
  // Highlighted outflow edges (green)
  if (edgesOut.length) {
    const lat = [], lon = [];
    for (const e of edgesOut) { lat.push(e.s_lat, e.t_lat, null); lon.push(e.s_lon, e.t_lon, null); }
    traces.push({
      type: "scattergeo", mode: "lines",
      lat, lon, line: { width: 2.2, color: "#2ca02c" },
      hoverinfo: "skip", name: `Outflows (${edgesOut.length})`,
    });
  }

  // Invisible midpoint markers for edge click — all edges so user can probe
  const midLat = [], midLon = [], midCustom = [], midHover = [];
  for (const e of EDGES) {
    midLat.push((e.s_lat + e.t_lat)/2);
    midLon.push((e.s_lon + e.t_lon)/2);
    midCustom.push({kind:"edge", source:e.source, target:e.target, via:e.via, value:e.value});
    midHover.push(`<b>${e.source} → ${e.target}</b><br>` +
                  (e.value !== null && e.value !== undefined ? fmt(e.value) : "latent"));
  }
  traces.push({
    type: "scattergeo", mode: "markers",
    lat: midLat, lon: midLon, hovertext: midHover, hoverinfo: "text",
    customdata: midCustom,
    marker: { size: 14, color: "rgba(0,0,0,0)", opacity: 0.001 },
    showlegend: false,
  });

  // Nodes per subtype
  const bySub = {};
  for (const nid in NODES) {
    const n = NODES[nid];
    (bySub[n.subtype] || (bySub[n.subtype] = [])).push(n);
  }
  for (const sub of Object.keys(SUBTYPE_COLOR)) {
    const ns = bySub[sub] || [];
    if (!ns.length) continue;
    traces.push({
      type: "scattergeo", mode: "markers",
      lat: ns.map(n => n.lat), lon: ns.map(n => n.lon),
      text: ns.map(n =>
        `<b>${n.asset_id}</b><br>${n.name || ""}<br>${SUBTYPE_LABEL[sub] || sub}<br>` +
        `${n.padd || ""} ${n.state ? '/ ' + n.state : ''}`),
      hoverinfo: "text",
      customdata: ns.map(n => ({kind:"node", asset_id: n.asset_id})),
      marker: {
        size: ns.map(n =>
          n.asset_id === selectedId ? 18 :
          (edgesIn.some(e => e.source === n.asset_id) || edgesOut.some(e => e.target === n.asset_id)) ? 12 : 7
        ),
        color: SUBTYPE_COLOR[sub],
        line: { width: ns.map(n => n.asset_id === selectedId ? 2 : 0.6),
                color: ns.map(n => n.asset_id === selectedId ? "#000" : "rgba(0,0,0,0.5)") },
        opacity: ns.map(n => isHi(n.asset_id) ? 0.95 : 0.18),
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
    if (!ev.points || !ev.points.length) return;
    const cd = ev.points[0].customdata;
    if (!cd) return;
    if (cd.kind === "node") {
      selectedId = cd.asset_id;
      sel.value = selectedId;
      render(); renderSide();
    } else if (cd.kind === "edge") {
      renderEdge(cd);
    }
  });
}

function renderEdge(cd) {
  const side = document.getElementById("side");
  side.innerHTML =
    `<h3>Flow edge</h3>
     <h2>${cd.source}<br><span style="color:#888">↓</span><br>${cd.target}</h2>
     <div class="row"><div class="k">variable</div><div class="v">${cd.via || ""}</div></div>
     <div class="row"><div class="k">value</div><div class="v">${fmt(cd.value)}</div></div>
     <p style="margin-top:8px;color:#666;font-size:11.5px">
       Click the source or target node to re-centre on it.
     </p>`;
}

function renderSide() {
  if (!selectedId) {
    document.getElementById("side").innerHTML =
      `<div class="placeholder">Pick a node above or click any marker.</div>`;
    return;
  }
  const n = NODES[selectedId];
  const ein = INFLOWS_TO[selectedId]   || [];
  const eou = OUTFLOWS_FROM[selectedId] || [];
  const sumIn  = ein.reduce((s, e) => s + (e.value || 0), 0);
  const sumOut = eou.reduce((s, e) => s + (e.value || 0), 0);
  const side = document.getElementById("side");
  side.innerHTML =
    `<h3>Node</h3>
     <h2>${n.asset_id}</h2>
     <div class="row"><div class="k">name</div><div class="v">${n.name || ""}</div></div>
     <div class="row"><div class="k">subtype</div><div class="v">${SUBTYPE_LABEL[n.subtype] || ""}</div></div>
     <div class="row"><div class="k">PADD/state</div><div class="v">${(n.padd || "") + " " + (n.state ? "/ " + n.state : "")}</div></div>
     <div class="row"><div class="k">coords</div><div class="v">${n.lat.toFixed(2)}, ${n.lon.toFixed(2)}${n.inferred ? " (inf)" : ""}</div></div>
     <h3>Inflows (${ein.length}) ← <span style="color:#ff7f0e">orange</span></h3>
     ${ein.length ? ein.map(e => `<div class="edge in" onclick="selectNode('${e.source}')">${e.source} <span class="val">${fmt(e.value)}</span></div>`).join("") : `<div class="edge"><i>no inflow edges</i></div>`}
     <div class="row" style="margin-top:6px"><div class="k">Σ inflows</div><div class="v">${fmt(sumIn)}</div></div>
     <h3>Outflows (${eou.length}) → <span style="color:#2ca02c">green</span></h3>
     ${eou.length ? eou.map(e => `<div class="edge out" onclick="selectNode('${e.target}')">${e.target} <span class="val">${fmt(e.value)}</span></div>`).join("") : `<div class="edge"><i>no outflow edges</i></div>`}
     <div class="row" style="margin-top:6px"><div class="k">Σ outflows</div><div class="v">${fmt(sumOut)}</div></div>`;
}

function selectNode(nid) {
  selectedId = nid;
  sel.value = nid;
  render(); renderSide();
}
window.selectNode = selectNode;

sel.onchange = (e) => { selectedId = e.target.value || null; render(); renderSide(); };
document.getElementById("clear").onclick = () => { selectedId = null; sel.value = ""; render(); renderSide(); };

render();
</script>
</body>
</html>
"""


def main(g: NetworkGraph = None):
    if g is None:
        g = NetworkGraph(SCENARIO)
    print(f"Using {g}")

    payload = build_payload(g)
    n_with_coords = len(payload["nodes"])
    print(f"  payload nodes: {n_with_coords}  payload edges: {len(payload['edges'])}")

    html = HTML_TEMPLATE
    html = html.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__SUBTYPE_COLOR__", json.dumps(SUBTYPE_COLOR))
    html = html.replace("__SUBTYPE_LABEL__", json.dumps(SUBTYPE_LABEL))
    html = html.replace("__DEFAULT_DATE__", DEFAULT_DATE)
    html = html.replace("__N_NODES__", str(n_with_coords))
    html = html.replace("__N_EDGES__", str(len(payload["edges"])))

    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
                              notes=f"physical-to-physical neighbours at {DEFAULT_DATE}")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)  "
          f"[artefact_id={artefact_id}, run_id={run_id}]")


if __name__ == "__main__":
    main()
