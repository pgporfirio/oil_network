"""Generate a self-contained HTML explorer for the oil_network time series.

Output: oil_network_explorer.html in the same directory.

Features:
 * Variable-type selector (production / consumption / inventory / inflow / outflow / balancing_item)
 * Hierarchy tree: USA -> PADD -> (district / production / refining / hubs / pipelines / ...)
 * Click a node to view its time series in a Plotly line chart
 * Leaves color-coded by binding: green=TS-bound, blue=derived formula, yellow=latent, gray=zero
 * Self-contained: all data embedded as JSON; Plotly.js loaded from CDN.

Idempotent.
"""
from __future__ import annotations
from paths import HTML_DIR

import json
import re
from collections import defaultdict
from pathlib import Path

import psycopg2

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_explorer.html"

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# ----------------------------------------------------------------------------- query

def fetch():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT v.variable_id, v.variable_type, v.commodity, v.node_id, v.related_node_id,
                   a.node_class, a.node_subtype, a.kind, a.name AS asset_name,
                   l.padd, l.country, l.state,
                   va.timeseries_id, va.formula
            FROM oil_network.variables v
            JOIN oil_network.assets a ON a.asset_id = v.node_id
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            LEFT JOIN oil_network.variable_assignments va ON va.variable_id = v.variable_id
            ORDER BY v.variable_id
        """)
        cols = [c.name for c in cur.description]
        variables = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT DISTINCT ON (timeseries_id, observation_date)
                   timeseries_id, observation_date::text, value
            FROM oil_network.timeseries_data
            ORDER BY timeseries_id, observation_date, saved_date DESC
        """)
        ts_data = defaultdict(list)
        for ts_id, obs_date, val in cur.fetchall():
            ts_data[ts_id].append([obs_date, val])

        cur.execute("""
            SELECT timeseries_id, name, unit FROM oil_network.timeseries
        """)
        ts_meta = {row[0]: {"name": row[1], "unit": row[2]} for row in cur.fetchall()}

    return variables, dict(ts_data), ts_meta


# ----------------------------------------------------------------------------- hierarchy

# Map node_id -> (top-level region, group-within-region). Top-level = USA / PADD 1..5 / unknown.
PADD_NAMES = {"1": "PADD 1 (East Coast)", "2": "PADD 2 (Midwest)",
              "3": "PADD 3 (Gulf Coast)", "4": "PADD 4 (Rocky Mtn)",
              "5": "PADD 5 (West Coast)"}

# Production / state node -> PADD (manual table; locations.padd covers physical but not abstract).
NODE_PADD_OVERRIDES = {
    "permian": "3", "permian_tx": "3", "permian_nm": "3",
    "bakken": "4", "bakken_nd": "2", "bakken_mt": "4",
    "eagle_ford": "3", "eagle_ford_tx": "3",
    "gulf_of_america": "3",
    "alaska_north_slope": "5",
    "california_conventional": "5",
    "oklahoma_conventional": "2",
    "wyoming_conventional": "4",
    "colorado_conventional": "4",
    "texas_other": "3",
    "montana_other": "4",
    "rest_of_l48": None,  # cross-PADD
    "canadian_oil_sands": None,  # foreign
    "foreign_export_destination": None,
}


def node_padd(var: dict) -> str | None:
    """Return '1'..'5' for a PADD, 'USA' for national, 'foreign' for boundary, None otherwise."""
    nid = var["node_id"]
    if nid.startswith("usa_") or nid == "spr_total":
        return "USA"
    m = re.match(r"padd(\d)_", nid)
    if m:
        return m.group(1)
    m = re.match(r"district_R([1-5])", nid)
    if m:
        return m.group(1) if m.group(1) != "E" else "1"
    if nid.startswith("district_REC") or nid.startswith("district_RAP"):
        return "1"
    if nid.startswith("district_R2"):
        return "2"
    if nid.startswith("district_R3"):
        return "3"
    if nid in NODE_PADD_OVERRIDES:
        return NODE_PADD_OVERRIDES[nid]
    if nid in ("canadian_oil_sands", "foreign_export_destination"):
        return "foreign"
    if var.get("padd"):
        # Stored as e.g. 'P3'
        m = re.match(r"P?([1-5])", str(var["padd"]))
        if m:
            return m.group(1)
    return None


def node_group(var: dict) -> str:
    """Group label within the PADD/USA branch."""
    nid = var["node_id"]
    sub = var.get("node_subtype") or ""
    cls = var.get("node_class") or ""

    if nid.startswith("usa_") or nid in ("spr_total",):
        return "National observation views"
    if re.match(r"padd\d_.*_view", nid) or sub in (
        "padd_view", "padd_imports_view", "padd_exports_view", "padd_stocks_view",
        "padd_canadian_inflow_view", "refining_centre_view"):
        return "PADD observation views"
    if sub == "refining_district_view":
        return "Refining districts"
    if sub == "refinery":
        return "Refineries"
    if sub == "spr_site":
        return "SPR sites"
    if sub == "storage_terminal":
        return "Storage hubs"
    if sub == "pipeline":
        return "Pipelines"
    if sub == "origin_terminal":
        return "Origin terminals"
    if sub == "import_terminal":
        return "Import terminals"
    if sub == "export_terminal":
        return "Export terminals"
    if sub == "gathering":
        return "Gathering"
    if sub == "offshore_region":
        return "Offshore production"
    if sub in ("state_conventional", "state_sub_basin", "state_residual",
               "state_view", "usa_subtotal_view"):
        return "Production (basins / states)"
    if sub == "foreign_export_destination":
        return "Foreign boundary"
    if sub == "foreign_production_aggregate":
        return "Foreign supply"
    return f"Other ({cls or 'unknown'})"


def binding_kind(var: dict) -> str:
    if var.get("timeseries_id"):
        return "ts"
    f = (var.get("formula") or "").strip()
    if f == "0":
        return "zero"
    if f == "latent()":
        return "latent"
    if f:
        return "formula"
    return "unassigned"


# ----------------------------------------------------------------------------- payload

def build_payload(variables, ts_data, ts_meta):
    # Group by variable_type so the UI filter is fast.
    by_vtype = defaultdict(list)
    for v in variables:
        # Skip relational variables in the tree view (inflow/outflow): they explode the tree
        # to per-edge granularity. Keep them queryable separately via an option.
        entry = {
            "variable_id":  v["variable_id"],
            "variable_type": v["variable_type"],
            "node_id":      v["node_id"],
            "related":      v["related_node_id"],
            "node_subtype": v["node_subtype"],
            "padd":         node_padd(v),
            "group":        node_group(v),
            "binding":      binding_kind(v),
            "timeseries_id": v["timeseries_id"],
            "formula":      v["formula"],
            "asset_name":   v["asset_name"],
        }
        by_vtype[v["variable_type"]].append(entry)

    # Only TS-bound variables have actual numbers to show. Embed the data.
    ts_payload = {}
    for ts_id, points in ts_data.items():
        if ts_id in ts_meta:
            ts_payload[ts_id] = {
                "points": points,
                "name":   ts_meta[ts_id]["name"],
                "unit":   ts_meta[ts_id]["unit"],
            }

    return {"variables": by_vtype, "ts": ts_payload}


# ----------------------------------------------------------------------------- html

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Oil Network Explorer</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {
    --bg: #f7f7f5; --panel: #ffffff; --border: #d0d0c8;
    --green: #2ca02c; --blue: #1f77b4; --yellow: #d4a017; --gray: #888;
    --hover: #eef3ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 13px/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: var(--bg); color: #222; }
  header { padding: 10px 18px; background: #1a1f2c; color: #fff;
           display: flex; gap: 18px; align-items: center; }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  header select { font: inherit; padding: 4px 8px; border-radius: 4px; border: 1px solid #444; }
  header .legend { margin-left: auto; font-size: 12px; color: #ccc; }
  header .legend span { display: inline-block; width: 10px; height: 10px;
                        border-radius: 2px; vertical-align: middle; margin: 0 4px 0 12px; }
  .layout { display: grid; grid-template-columns: 380px 1fr; height: calc(100vh - 50px); }
  .tree-pane { overflow-y: auto; background: var(--panel); border-right: 1px solid var(--border);
               padding: 8px 0; }
  .chart-pane { padding: 18px; overflow-y: auto; }
  details { padding: 2px 8px; }
  summary { cursor: pointer; user-select: none; padding: 2px 4px; border-radius: 3px; }
  summary:hover { background: var(--hover); }
  summary .count { color: #999; font-size: 11px; margin-left: 4px; }
  ul.leaves { list-style: none; padding-left: 20px; margin: 2px 0 6px 0; }
  ul.leaves li { padding: 1px 6px 1px 18px; cursor: pointer; border-radius: 3px;
                 position: relative; font-family: ui-monospace, "SF Mono", Menlo, monospace;
                 font-size: 12px; line-height: 1.55; }
  ul.leaves li::before { content: ""; position: absolute; left: 6px; top: 7px;
                         width: 8px; height: 8px; border-radius: 50%; }
  ul.leaves li.ts::before     { background: var(--green); }
  ul.leaves li.formula::before { background: var(--blue); }
  ul.leaves li.latent::before  { background: var(--yellow); }
  ul.leaves li.zero::before    { background: var(--gray); }
  ul.leaves li:hover { background: var(--hover); }
  ul.leaves li.selected { background: #e0e8ff; font-weight: 600; }
  #chart { width: 100%; height: 60vh; }
  .meta { margin-top: 12px; padding: 10px 14px; background: var(--panel);
          border: 1px solid var(--border); border-radius: 4px; font-size: 12px;
          font-family: ui-monospace, "SF Mono", Menlo, monospace; white-space: pre-wrap; }
  .placeholder { color: #999; padding: 40px; text-align: center; font-style: italic; }
</style>
</head>
<body>
<header>
  <h1>oil_network &middot; time-series explorer</h1>
  <label>variable
    <select id="vtype">
      <option value="production">production</option>
      <option value="consumption">consumption</option>
      <option value="inventory">inventory</option>
      <option value="inflow">inflow (per-edge)</option>
      <option value="outflow">outflow (per-edge)</option>
      <option value="balancing_item">balancing_item</option>
    </select>
  </label>
  <div class="legend">
    <span style="background:var(--green)"></span>TS-bound
    <span style="background:var(--blue)"></span>formula
    <span style="background:var(--yellow)"></span>latent
    <span style="background:var(--gray)"></span>zero
  </div>
</header>
<div class="layout">
  <div class="tree-pane" id="tree"></div>
  <div class="chart-pane">
    <div id="chart"></div>
    <div class="meta" id="meta"></div>
  </div>
</div>
<script>
const DATA = __DATA__;

const PADD_LABEL = {
  "USA": "USA (national)",
  "1": "PADD 1 (East Coast)", "2": "PADD 2 (Midwest)",
  "3": "PADD 3 (Gulf Coast)", "4": "PADD 4 (Rocky Mtn)",
  "5": "PADD 5 (West Coast)",
  "foreign": "Foreign / boundary",
  "null":    "Unclassified",
};

function buildTree(vtype) {
  const items = DATA.variables[vtype] || [];
  // Group by padd then by group
  const groups = {};
  for (const v of items) {
    const p = v.padd === null ? "null" : String(v.padd);
    if (!groups[p]) groups[p] = {};
    const g = v.group;
    if (!groups[p][g]) groups[p][g] = [];
    groups[p][g].push(v);
  }
  const orderedPADD = ["USA", "1", "2", "3", "4", "5", "foreign", "null"];
  const tree = document.getElementById("tree");
  tree.innerHTML = "";
  for (const p of orderedPADD) {
    if (!groups[p]) continue;
    const total = Object.values(groups[p]).reduce((n, arr) => n + arr.length, 0);
    if (total === 0) continue;
    const d = document.createElement("details");
    d.open = (p === "USA" || p === "1" || p === "2" || p === "3");
    const s = document.createElement("summary");
    s.innerHTML = `<b>${PADD_LABEL[p] || p}</b> <span class="count">${total}</span>`;
    d.appendChild(s);
    const groupNames = Object.keys(groups[p]).sort();
    for (const g of groupNames) {
      const arr = groups[p][g];
      const dg = document.createElement("details");
      dg.open = arr.length <= 8;
      const sg = document.createElement("summary");
      sg.innerHTML = `${g} <span class="count">${arr.length}</span>`;
      dg.appendChild(sg);
      const ul = document.createElement("ul");
      ul.className = "leaves";
      arr.sort((a, b) => a.variable_id.localeCompare(b.variable_id));
      for (const v of arr) {
        const li = document.createElement("li");
        li.className = v.binding;
        const label = v.related
          ? `${v.node_id} → ${v.related}`
          : v.node_id;
        li.textContent = label;
        li.title = v.variable_id;
        li.onclick = () => selectVar(v, li);
        ul.appendChild(li);
      }
      dg.appendChild(ul);
      d.appendChild(dg);
    }
    tree.appendChild(d);
  }
}

let _selected = null;
function selectVar(v, li) {
  if (_selected) _selected.classList.remove("selected");
  _selected = li;
  li.classList.add("selected");
  const meta = document.getElementById("meta");

  if (v.binding === "ts" && v.timeseries_id && DATA.ts[v.timeseries_id]) {
    const ts = DATA.ts[v.timeseries_id];
    const x = ts.points.map(p => p[0]);
    const y = ts.points.map(p => p[1]);
    const isLevel = (ts.unit === "mbbl_level");
    Plotly.newPlot("chart", [{
      x, y, type: "scatter", mode: "lines+markers",
      line: { color: "#1f77b4", width: 1.5 },
      marker: { size: 4 },
      name: v.variable_id
    }], {
      title: `${v.variable_id}<br><sub>${ts.name}</sub>`,
      xaxis: { title: "date", type: "date" },
      yaxis: { title: ts.unit + (isLevel ? " (stock level)" : "") },
      margin: { l: 60, r: 30, t: 80, b: 50 },
      hovermode: "x unified",
    }, { responsive: true });
    meta.textContent =
      `variable_id    : ${v.variable_id}\n` +
      `timeseries_id  : ${v.timeseries_id}\n` +
      `series name    : ${ts.name}\n` +
      `unit           : ${ts.unit}\n` +
      `points         : ${ts.points.length}\n` +
      `range          : ${x[0]}  ->  ${x[x.length - 1]}\n` +
      `binding        : TS-bound (authoritative observation)`;
  } else {
    document.getElementById("chart").innerHTML =
      `<div class="placeholder">No time series for this variable.<br>` +
      `Binding: <b>${v.binding}</b>` +
      (v.formula ? ` &middot; formula = <code>${v.formula}</code>` : "") +
      `</div>`;
    meta.textContent =
      `variable_id    : ${v.variable_id}\n` +
      `binding        : ${v.binding}\n` +
      (v.formula ? `formula        : ${v.formula}\n` : "");
  }
}

document.getElementById("vtype").addEventListener("change", e => {
  buildTree(e.target.value);
  document.getElementById("chart").innerHTML =
    `<div class="placeholder">Select a variable from the tree on the left.</div>`;
  document.getElementById("meta").textContent = "";
});

// initial render
buildTree("production");
document.getElementById("chart").innerHTML =
  `<div class="placeholder">Select a variable from the tree on the left.</div>`;
</script>
</body>
</html>
"""


def main():
    print("Querying DB...")
    variables, ts_data, ts_meta = fetch()
    print(f"  variables: {len(variables)}, ts series with data: {len(ts_data)}")

    payload = build_payload(variables, ts_data, ts_meta)
    n_per_type = {k: len(v) for k, v in payload["variables"].items()}
    print(f"  variables by type: {n_per_type}")
    print(f"  embedded TS series: {len(payload['ts'])}")

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    HTML_OUT.write_text(html, encoding="utf-8")
    size_kb = HTML_OUT.stat().st_size // 1024
    print(f"Wrote {HTML_OUT}  ({size_kb} KB)")
    print("Open the file in a browser. Plotly loads from CDN; if offline, use a cached plotly-2.35.2.min.js.")


if __name__ == "__main__":
    main()
