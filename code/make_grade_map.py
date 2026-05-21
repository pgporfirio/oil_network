"""Grade footprint map.

Output: oil_network_grade_map.html

Same Plotly natural-earth map as oil_network_node_neighbors.html. Two
controls at the top:

  - Grade selector (one of the 18 US crude grades + 'crude' itself)
  - Date selector (the resolved-date axis)

On grade change:
  - Nodes that carry the grade (have any non-null resolved per-grade value
    at the selected date) light up in red proportional to their production
    or pass-through flow magnitude.
  - Nodes that have the grade variable but no resolved value at this date
    (latent) light up in faint orange.
  - Edges where the grade has a non-null outflow are drawn in red.
  - Everything else is dimmed.

Side panel: top-N nodes by value for the selected grade at the selected
date, with the production / inflow / outflow / consumption / inventory
breakdown per node.

Scenario: crude_starter_with_grades (the one with per-grade variables).
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone

import psycopg2
from paths import HTML_DIR
from render_utils import latest_run_id, write_html

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
HTML_OUT = HTML_DIR / "oil_network_grade_map.html"
SCENARIO = "crude_starter_with_grades"
VIEW_NAME = "grade_map"


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


def fetch():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # Physical nodes with coords
        cur.execute("""
            SELECT a.asset_id, a.name, a.node_subtype, l.lat, l.lon, l.state, l.padd
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.kind = 'physical' AND l.lat IS NOT NULL
            ORDER BY a.asset_id
        """)
        nodes = {r[0]: {"name": r[1] or r[0], "subtype": r[2],
                        "lat": r[3], "lon": r[4], "state": r[5], "padd": r[6]}
                 for r in cur.fetchall()}

        # Edges (physical-to-physical) — directed
        cur.execute("""
            SELECT DISTINCT v.node_id, v.related_node_id
            FROM oil_network.variables v
            JOIN oil_network.assets s ON s.asset_id = v.node_id AND s.kind = 'physical'
            JOIN oil_network.assets t ON t.asset_id = v.related_node_id AND t.kind = 'physical'
            WHERE v.variable_type = 'outflow' AND v.commodity = 'crude'
        """)
        edges = [{"source": r[0], "target": r[1]} for r in cur.fetchall()
                 if r[0] in nodes and r[1] in nodes]
        for e in edges:
            e["s_lat"] = nodes[e["source"]]["lat"]; e["s_lon"] = nodes[e["source"]]["lon"]
            e["t_lat"] = nodes[e["target"]]["lat"]; e["t_lon"] = nodes[e["target"]]["lon"]

        # Grade list (children of crude in commodity_hierarchy, plus crude itself)
        cur.execute("""SELECT child_commodity FROM oil_network.commodity_hierarchy
                       WHERE parent_commodity = 'crude' ORDER BY child_commodity""")
        grades = ["crude"] + [r[0] for r in cur.fetchall()]

        # Dates
        cur.execute("""SELECT DISTINCT observation_date FROM oil_network.scenario_resolved_values
                       WHERE scenario_id = %s ORDER BY observation_date""", (SCENARIO,))
        dates = [r[0].isoformat() for r in cur.fetchall()]

        # Per (grade, node, date): one aggregate value to show on the map.
        # Aggregation rule: pick whichever is most informative —
        #   - For producer nodes: production
        #   - For sink nodes (refineries, export terminals): consumption / outflow
        #   - For pass-through: sum of outflows (= sum of inflows)
        # Simplification: use SUM(outflow) for relational presence + production
        # for non-relational presence, capped at the maximum of either at the node.
        # Implementation: just take max over (production, sum_outflow, sum_inflow,
        # consumption) per (node, grade, date) to get a single number.
        cur.execute("""
            WITH per_var AS (
              SELECT v.commodity AS grade, v.node_id, v.variable_type, srv.observation_date,
                     SUM(srv.value) AS v
              FROM oil_network.variables v
              JOIN oil_network.scenario_resolved_values srv ON srv.variable_id = v.variable_id
              JOIN oil_network.assets a ON a.asset_id = v.node_id
              WHERE srv.scenario_id = %s AND a.kind = 'physical'
                AND v.variable_type IN ('production','consumption','inflow','outflow')
              GROUP BY v.commodity, v.node_id, v.variable_type, srv.observation_date
            )
            SELECT grade, node_id, observation_date,
                   MAX(v) FILTER (WHERE variable_type = 'production')  AS p,
                   MAX(v) FILTER (WHERE variable_type = 'consumption') AS c,
                   MAX(v) FILTER (WHERE variable_type = 'inflow')      AS i,
                   MAX(v) FILTER (WHERE variable_type = 'outflow')     AS o
            FROM per_var GROUP BY grade, node_id, observation_date
        """, (SCENARIO,))
        # presence[grade][node][date] = {p, c, i, o, m} where m is the magnitude to map
        presence: dict = defaultdict(lambda: defaultdict(dict))
        for grade, node, d, p, c, i, o in cur.fetchall():
            if node not in nodes:
                continue
            d_str = d.isoformat()
            mag = max((x for x in (p, c, i, o) if x is not None), default=None)
            presence[grade][node][d_str] = {
                "p": float(p) if p is not None else None,
                "c": float(c) if c is not None else None,
                "i": float(i) if i is not None else None,
                "o": float(o) if o is not None else None,
                "m": float(mag) if mag is not None else None,
            }

        # Per (grade, edge): does outflow_g(s, t) have any resolved non-null value
        # at the selected date? Embed only the binary presence per (grade, date).
        cur.execute("""
            SELECT v.commodity, v.node_id, v.related_node_id, srv.observation_date
            FROM oil_network.variables v
            JOIN oil_network.scenario_resolved_values srv ON srv.variable_id = v.variable_id
            JOIN oil_network.assets s ON s.asset_id = v.node_id AND s.kind = 'physical'
            JOIN oil_network.assets t ON t.asset_id = v.related_node_id AND t.kind = 'physical'
            WHERE srv.scenario_id = %s AND v.variable_type = 'outflow' AND srv.value IS NOT NULL
        """, (SCENARIO,))
        # edges_active[grade][date_str] = list of "source>target" keys
        edges_active: dict = defaultdict(lambda: defaultdict(set))
        for grade, s, t, d in cur.fetchall():
            edges_active[grade][d.isoformat()].add(f"{s}>{t}")
        # Convert sets to lists for JSON
        edges_active_out = {g: {d: sorted(s) for d, s in dd.items()}
                            for g, dd in edges_active.items()}

        # Convert presence to plain dicts
        presence_out = {g: {n: dict(dd) for n, dd in nd.items()}
                        for g, nd in presence.items()}

    return {
        "nodes": nodes, "edges": edges, "grades": grades, "dates": dates,
        "presence": presence_out, "edges_active": edges_active_out,
    }


HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>oil_network &mdash; grade footprint</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { margin: 0; font: 13px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; background: #f7f7f5; color: #222; }
  header { padding: 8px 16px; background: #1a1f2c; color: #fff;
           display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; }
  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .controls label { color: #ccc; font-size: 11.5px; }
  .controls select, .controls input { background: #fff; color: #222; border: 1px solid #444;
                                       padding: 3px 6px; font: inherit; }
  header .stats { margin-left: auto; font-size: 11.5px; color: #ccc; }
  .layout { display: grid; grid-template-columns: 1fr 380px; height: calc(100vh - 38px); }
  #map { width: 100%; height: 100%; }
  #side { background: #fff; border-left: 1px solid #d0d0c8; padding: 14px 16px;
          overflow-y: auto; font-size: 12.5px; }
  #side h2 { margin: 0 0 6px 0; font-size: 14px; font-family: ui-monospace, Menlo, monospace; }
  #side h3 { margin: 14px 0 4px 0; font-size: 12px; color: #666; text-transform: uppercase;
             letter-spacing: 0.04em; }
  #side table { width: 100%; border-collapse: collapse; font-size: 11.5px; font-family: ui-monospace, Menlo, monospace; }
  #side td, #side th { padding: 3px 4px; text-align: right; border-bottom: 1px solid #f0f0eb; }
  #side th:first-child, #side td:first-child { text-align: left; }
  #side th { font-size: 10.5px; color: #777; }
  #side .placeholder { color: #999; padding: 30px 0; text-align: center; font-style: italic; }
  #side .summary { font-size: 12px; color: #444; margin-bottom: 10px; }
  #side .summary b { color: #d62728; }
</style></head><body>
<header>
  <h1>oil_network &middot; grade footprint</h1>
  <div class="controls">
    <label>Grade:</label>
    <select id="grade-select"></select>
    <label>Date:</label>
    <select id="date-select"></select>
  </div>
  <div class="stats" id="header-stats"></div>
</header>
<div class="layout">
  <div id="map"></div>
  <aside id="side"><div class="placeholder">Pick a grade above to see its footprint.</div></aside>
</div>
<script>
const DATA = __DATA__;
const SUBTYPE_COLOR = __SUBTYPE_COLOR__;
const SUBTYPE_LABEL = __SUBTYPE_LABEL__;
const NODES = DATA.nodes, EDGES = DATA.edges, GRADES = DATA.grades, DATES = DATA.dates;
const PRESENCE = DATA.presence, EDGES_ACTIVE = DATA.edges_active;

const gradeSel = document.getElementById("grade-select");
for (const g of GRADES) {
  const o = document.createElement("option"); o.value = g; o.textContent = g; gradeSel.appendChild(o);
}
const dateSel = document.getElementById("date-select");
for (const d of DATES) {
  const o = document.createElement("option"); o.value = d; o.textContent = d; dateSel.appendChild(o);
}
dateSel.value = DATES.includes("2024-12-01") ? "2024-12-01" : DATES[DATES.length - 1];
gradeSel.value = "wti_midland";

function fmt(v) {
  if (v === null || v === undefined) return "-";
  if (Math.abs(v) < 0.005) return "0";
  return v.toLocaleString(undefined, {maximumFractionDigits: 1});
}

function render() {
  const grade = gradeSel.value;
  const date = dateSel.value;
  const gradeNodes = PRESENCE[grade] || {};
  const activeEdges = new Set((EDGES_ACTIVE[grade] || {})[date] || []);

  // Per-node status: 'active' (non-null value at date), 'declared' (variable exists but null/latent), 'absent'
  const nodeStatus = {};
  let n_active = 0, n_declared = 0;
  for (const nid in NODES) {
    const at = (gradeNodes[nid] || {})[date];
    if (at && at.m !== null && at.m !== undefined) { nodeStatus[nid] = "active"; n_active++; }
    else if (gradeNodes[nid]) { nodeStatus[nid] = "declared"; n_declared++; }
    else { nodeStatus[nid] = "absent"; }
  }
  document.getElementById("header-stats").innerHTML =
    `<b>${n_active}</b> nodes active &middot; <b>${n_declared}</b> declared-latent &middot; ` +
    `<b>${activeEdges.size}</b> edges carrying grade`;

  const traces = [];

  // Dim background edges (all of them)
  const dimLat = [], dimLon = [];
  for (const e of EDGES) {
    const key = `${e.source}>${e.target}`;
    if (activeEdges.has(key)) continue;
    dimLat.push(e.s_lat, e.t_lat, null); dimLon.push(e.s_lon, e.t_lon, null);
  }
  if (dimLat.length) traces.push({
    type: "scattergeo", mode: "lines", lat: dimLat, lon: dimLon,
    line: { width: 0.5, color: "rgba(60,60,60,0.10)" },
    hoverinfo: "skip", showlegend: false,
  });

  // Active edges in red
  if (activeEdges.size) {
    const lat = [], lon = [];
    for (const e of EDGES) {
      const key = `${e.source}>${e.target}`;
      if (!activeEdges.has(key)) continue;
      lat.push(e.s_lat, e.t_lat, null); lon.push(e.s_lon, e.t_lon, null);
    }
    traces.push({
      type: "scattergeo", mode: "lines", lat, lon,
      line: { width: 1.8, color: "#d62728" },
      hoverinfo: "skip", name: `${grade} flow edges (${activeEdges.size})`,
    });
  }

  // Nodes by subtype with status-based styling
  const bySub = {};
  for (const nid in NODES) {
    const n = NODES[nid];
    (bySub[n.subtype] || (bySub[n.subtype] = [])).push(nid);
  }
  for (const sub of Object.keys(SUBTYPE_COLOR)) {
    const nids = bySub[sub] || [];
    if (!nids.length) continue;
    traces.push({
      type: "scattergeo", mode: "markers",
      lat: nids.map(n => NODES[n].lat), lon: nids.map(n => NODES[n].lon),
      text: nids.map(nid => {
        const n = NODES[nid];
        const at = (gradeNodes[nid] || {})[date];
        const valStr = at && at.m !== null && at.m !== undefined ? `<br><b>${grade}</b>: ${fmt(at.m)} kbd` : "";
        return `<b>${nid}</b><br>${n.name}<br>${SUBTYPE_LABEL[sub] || sub}${valStr}`;
      }),
      hoverinfo: "text",
      customdata: nids.map(n => ({asset_id: n})),
      marker: {
        size: nids.map(nid => {
          const st = nodeStatus[nid];
          if (st === "active") {
            const at = gradeNodes[nid][date];
            // Size proportional to magnitude (log-ish scale)
            const m = at.m || 0;
            return 6 + Math.min(20, Math.sqrt(m) * 0.8);
          }
          if (st === "declared") return 5;
          return 4;
        }),
        color: nids.map(nid => {
          const st = nodeStatus[nid];
          if (st === "active")   return "#d62728";  // red
          if (st === "declared") return "#ff9d6a";  // faint orange
          return SUBTYPE_COLOR[sub];
        }),
        opacity: nids.map(nid => {
          const st = nodeStatus[nid];
          if (st === "active")   return 0.95;
          if (st === "declared") return 0.55;
          return 0.18;
        }),
        line: { width: nids.map(nid => nodeStatus[nid] === "active" ? 1.5 : 0.3),
                color: "rgba(0,0,0,0.6)" },
      },
      name: (SUBTYPE_LABEL[sub] || sub) + " (" + nids.length + ")",
      showlegend: false,
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
  };
  Plotly.react("map", traces, layout, { responsive: true });

  // Side panel: top-N nodes by magnitude for this grade at this date
  const side = document.getElementById("side");
  const sortedActive = Object.entries(gradeNodes)
    .map(([nid, byDate]) => [nid, byDate[date]])
    .filter(([_, at]) => at && at.m !== null && at.m !== undefined)
    .sort((a, b) => b[1].m - a[1].m);

  if (!sortedActive.length) {
    side.innerHTML = `<h2>${grade}</h2><div class="placeholder">No active nodes for ${grade} on ${date}.<br>` +
                     `(${n_declared} declared-latent: variable exists but value is NULL.)</div>`;
    return;
  }

  const rows = sortedActive.slice(0, 50).map(([nid, at]) => {
    const n = NODES[nid];
    return `<tr><td>${nid}</td><td>${fmt(at.p)}</td><td>${fmt(at.i)}</td><td>${fmt(at.o)}</td><td>${fmt(at.c)}</td></tr>`;
  }).join("");

  side.innerHTML = `
    <h2>${grade}</h2>
    <div class="summary"><b>${sortedActive.length}</b> active nodes &middot; <b>${activeEdges.size}</b> active edges &middot; <i>${date}</i></div>
    <h3>Top nodes by magnitude (kbd)</h3>
    <table>
      <thead><tr><th>node</th><th>P</th><th>F_in</th><th>F_out</th><th>C</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

gradeSel.onchange = render; dateSel.onchange = render;
render();
</script></body></html>
"""


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    data = fetch()
    print(f"  {len(data['nodes'])} physical nodes, {len(data['edges'])} edges, "
          f"{len(data['grades'])} grades, {len(data['dates'])} dates")
    html = (HTML
            .replace("__DATA__", json.dumps(data, separators=(",", ":"), default=float))
            .replace("__SUBTYPE_COLOR__", json.dumps(SUBTYPE_COLOR))
            .replace("__SUBTYPE_LABEL__", json.dumps(SUBTYPE_LABEL)))
    run_id = latest_run_id(SCENARIO)
    write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
               notes=f"{len(data['grades'])} grades x {len(data['dates'])} dates")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
