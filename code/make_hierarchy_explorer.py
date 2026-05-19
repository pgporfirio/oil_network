"""Generate a self-contained HTML hierarchy explorer.

Output: oil_network_hierarchy.html in the same directory.

Tree structure:
 * Roots = abstract nodes with no aggregation parent (usa_view, foreign nodes, basin aggregates)
 * Drill-down: USA -> PADDs -> districts/state-views/PADD-residuals -> physical assets
 * Parent inference for each node:
     PRIMARY: appears in formula_inputs of another variable -> child of that variable's node
     FALLBACK: physical node with a location.padd -> child of padd<X>_view
 * Each node shows: kind (physical/abstract), node_subtype, variables, TS bindings,
   parent source (formula/geography), and whether it's a connectedness orphan.

Visual flags:
 *  green dot = has at least one TS-bound variable
 *  yellow dot = no data, but has aggregation/geographic parent
 *  red dot = ORPHAN (no data + no parent)

Idempotent. Plotly loaded via CDN for the per-node TS chart.
"""
from __future__ import annotations
from paths import HTML_DIR

import json
import re
from collections import defaultdict
from pathlib import Path

import psycopg2

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_hierarchy.html"

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"


# ============================================================================= query

def fetch():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.name, a.kind, a.node_class, a.node_subtype,
                   l.padd, l.state, l.country
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        assets = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT v.variable_id, v.variable_type, v.commodity, v.node_id, v.related_node_id,
                   va.timeseries_id, va.formula, va.formula_inputs
            FROM oil_network.variables v
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

        cur.execute("SELECT timeseries_id, name, unit FROM oil_network.timeseries")
        ts_meta = {row[0]: {"name": row[1], "unit": row[2]} for row in cur.fetchall()}

        # Read the canonical status view: authoritative / derived / collapsed
        # per (scenario, node), inferred from variable_assignments
        # (Principle 2.4 / 2.8).
        cur.execute("""
            SELECT node_id, status, n_ts, n_derived, n_zero, n_latent
            FROM oil_network.v_node_status
            WHERE scenario_id = %s
        """, (SCENARIO,))
        node_status_map = {
            r[0]: {"status": r[1], "n_ts": r[2], "n_derived": r[3],
                   "n_zero": r[4], "n_latent": r[5]}
            for r in cur.fetchall()
        }

    return assets, variables, dict(ts_data), ts_meta, node_status_map


# ============================================================================= parent inference

# Per-node-id explicit PADD assignments for nodes lacking location.padd metadata.
# State-residual nodes (padd*_other), state views, and a handful of structural
# aggregates need this since they aren't in oil_network.locations.
EXPLICIT_PADD = {
    "padd1_other": "1", "padd2_other": "2", "padd3_other": "3", "padd4_other": "4", "padd5_other": "5",
    "texas_state_view": "3", "montana_state_view": "4",
    "permian_nm": "3",  # NM is in PADD 3
}

# Basin aggregates are cross-PADD or PADD-3 (Permian, Eagle Ford) / cross-PADD (Bakken).
# We attach them under usa_view as "Basins" group rather than a single PADD.
BASIN_NODES = {"permian", "bakken", "eagle_ford"}

# Top-level abstract roots that don't have a parent.
ROOT_NODES = {"usa_view", "canadian_oil_sands", "foreign_supply", "foreign_export_destination"}


def derive_padd(asset: dict) -> str | None:
    """Return '1'..'5' for nodes geographically in a PADD, else None."""
    nid = asset["asset_id"]
    if nid in EXPLICIT_PADD:
        return EXPLICIT_PADD[nid]
    # location.padd is stored like 'PADD2', 'P3', or '3' depending on source
    p = asset.get("padd")
    if p:
        m = re.search(r"([1-5])", str(p))
        if m:
            return m.group(1)
    # node_id-based fallback: padd<N>_*
    m = re.match(r"padd([1-5])_", nid)
    if m:
        return m.group(1)
    m = re.match(r"district_R([1-5])", nid)
    if m:
        return m.group(1)
    if nid.startswith("district_REC") or nid.startswith("district_RAP"):
        return "1"
    return None


def build_parent_map(assets, variables):
    """Compute parent for each node from `v_partition_tree`.

    Eighteenth-pass simplification: the hierarchy is purely derived from the
    variables (Principle 2.4 / 2.5). Earlier versions of this function
    carried a 30-entry `structural` dict, an `EXPLICIT_PADD` map, and a
    `derive_padd()` regex fallback. All three are gone — the data layer
    now encodes every structural relation via `formula_inputs` and the
    `v_partition_tree` materialised view is the single source of truth.

    Constraint / observational aggregates (basin views, state views, SPR
    total) sit outside the partition spine by design. The eighteenth-pass
    data migration added them as inventory-children of their natural
    display parent (zero numerical effect, structural display only), so
    they show up in `v_partition_tree` like any other partition member.

    Returns: {child_node_id: (parent_node_id, 'formula_inputs')}.
    """
    # Primary: read v_partition_tree (single source of truth, derived from
    # variables). The renderer needs ONE parent per child; pick the
    # alphabetically-first as "primary" — the multi-parent rendering in
    # make_hierarchy_resolver_ui adds the node to ALL parents' children
    # lists after this map is built.
    #
    # Fallback: physical nodes with a `locations.padd` label that aren't
    # in v_partition_tree get attached under their PADD. This is purely
    # data-driven (locations.padd is the source of truth, not a hardcoded
    # dict). It exists because some terminal/pipeline nodes carry no
    # `formula_inputs` reference from any aggregate, but they geographically
    # live in a PADD and should display there.
    import psycopg2, re
    DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
    SCENARIO = "starter_us_crude_2015_2025"
    parent: dict[str, tuple[str, str]] = {}
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT child_node_id, MIN(parent_node_id) AS parent_node_id
            FROM oil_network.v_partition_tree
            WHERE scenario_id = %s
            GROUP BY child_node_id
        """, (SCENARIO,))
        for child, par in cur.fetchall():
            parent[child] = (par, "formula_inputs")

        # Geographic fallback: physical nodes with location.padd that aren't
        # otherwise placed. Reads location data directly (not from a
        # hardcoded map).
        cur.execute("""
            SELECT a.asset_id, l.padd
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.kind = 'physical' AND l.padd IS NOT NULL
        """)
        for nid, padd_raw in cur.fetchall():
            if nid in parent:
                continue
            m = re.search(r"([1-5])", str(padd_raw or ""))
            if m:
                parent[nid] = (f"padd{m.group(1)}_view", "geography")
    return parent


# ============================================================================= payload

def build_payload(assets, variables, ts_data, ts_meta, parent_map, node_status_map):
    by_id = {a["asset_id"]: a for a in assets}

    # Variables per node
    vars_by_node = defaultdict(list)
    for v in variables:
        vars_by_node[v["node_id"]].append({
            "variable_id":  v["variable_id"],
            "variable_type": v["variable_type"],
            "related_node": v["related_node_id"],
            "timeseries_id": v["timeseries_id"],
            "formula": v["formula"],
        })

    # Compute children for each node
    children = defaultdict(list)
    for child, (par, _src) in parent_map.items():
        children[par].append(child)

    # Status comes from v_node_status (Principle 2.4 / 2.8). The view labels
    # each (scenario, node) as authoritative / derived / collapsed based on
    # variable_assignments — own TS counts make a node authoritative; a
    # non-trivial formula makes it derived; otherwise it is collapsed (all
    # zero/latent). A node with no row at all in the view is structural-only
    # (no assignments under this scenario) — treated as orphan unless it is
    # a designated root.
    def node_status(nid):
        row = node_status_map.get(nid)
        if row is None:
            return "root" if nid in ROOT_NODES else "orphan"
        s = row["status"]
        if s == "authoritative":
            return "ts"
        if s == "derived":
            return "derived"
        # collapsed: no own TS, only zero/latent formulas — but if the node
        # has aggregation children in v_partition_tree, its inventory will
        # still be derived from them at resolve time (sum rule). Treat it as
        # derived for the purpose of the badge.
        if children.get(nid):
            return "derived"
        if nid in ROOT_NODES:
            return "root"
        return "collapsed"

    # Build node dicts
    nodes_payload = {}
    for a in assets:
        nid = a["asset_id"]
        nodes_payload[nid] = {
            "name":     a["name"],
            "kind":     a["kind"],
            "node_class": a["node_class"],
            "node_subtype": a["node_subtype"],
            "padd":     a.get("padd"),
            "state":    a.get("state"),
            "country":  a.get("country"),
            "variables": vars_by_node.get(nid, []),
            "parent":   parent_map.get(nid, [None, None])[0],
            "parent_source": parent_map.get(nid, [None, None])[1],
            "children": sorted(children.get(nid, [])),
            "status":   node_status(nid),
        }

    # Roots = nodes with no parent
    roots = sorted([nid for nid in nodes_payload if nodes_payload[nid]["parent"] is None])

    # Embed only TS data points
    ts_payload = {}
    for ts_id, points in ts_data.items():
        if ts_id in ts_meta:
            ts_payload[ts_id] = {
                "points": points,
                "name":   ts_meta[ts_id]["name"],
                "unit":   ts_meta[ts_id]["unit"],
            }

    # Audit summary
    audit = {
        "n_nodes":     len(assets),
        "n_physical":  sum(1 for a in assets if a["kind"] == "physical"),
        "n_abstract":  sum(1 for a in assets if a["kind"] == "abstract"),
        "n_with_data": sum(1 for nid in nodes_payload if nodes_payload[nid]["status"] == "ts"),
        "n_derived":   sum(1 for nid in nodes_payload if nodes_payload[nid]["status"] == "derived"),
        "n_collapsed": sum(1 for nid in nodes_payload if nodes_payload[nid]["status"] == "collapsed"),
        "n_orphans":   sum(1 for nid in nodes_payload if nodes_payload[nid]["status"] == "orphan"),
        "roots":       roots,
    }

    return {"nodes": nodes_payload, "ts": ts_payload, "audit": audit, "roots": roots}


# ============================================================================= html

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>oil_network &mdash; hierarchy explorer</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {
    --bg: #f7f7f5; --panel: #ffffff; --border: #d0d0c8;
    --green: #2ca02c; --blue: #1f77b4; --grey: #999; --red: #d62728;
    --abstract: #6a5acd; --physical: #2a9d8f;
    --hover: #eef3ff; --selected: #c8d4f7;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 13px/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: var(--bg); color: #222; }
  header { padding: 10px 18px; background: #1a1f2c; color: #fff;
           display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  header .audit { font-size: 11.5px; color: #ccc; margin-left: auto; }
  header .audit b { color: #fff; }
  .layout { display: grid; grid-template-columns: 420px 1fr; height: calc(100vh - 50px); }
  .tree-pane { overflow-y: auto; background: var(--panel); border-right: 1px solid var(--border);
               padding: 8px 0; font-size: 12.5px; }
  .detail-pane { padding: 18px; overflow-y: auto; }
  details { padding: 1px 8px; }
  summary { cursor: pointer; user-select: none; padding: 1px 4px; border-radius: 3px;
            font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  summary:hover { background: var(--hover); }
  summary.selected { background: var(--selected); font-weight: 600; }
  summary .badge {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    margin-right: 5px; vertical-align: middle;
  }
  summary .badge.ts        { background: var(--green); }
  summary .badge.derived   { background: var(--blue); }
  summary .badge.collapsed { background: var(--grey); }
  summary .badge.root      { background: #555; }
  summary .badge.orphan    { background: var(--red); }
  summary .kind-tag {
    display: inline-block; padding: 0 4px; border-radius: 2px;
    font-size: 9.5px; font-family: -apple-system, "Segoe UI", sans-serif;
    margin-left: 4px; color: white; opacity: 0.8;
  }
  summary .kind-tag.abstract { background: var(--abstract); }
  summary .kind-tag.physical { background: var(--physical); }
  summary .count { color: #999; font-size: 11px; margin-left: 4px; font-family: -apple-system, sans-serif; }
  ul.leaves { list-style: none; padding-left: 16px; margin: 1px 0 4px 0; }
  ul.leaves li { padding: 1px 4px 1px 14px; cursor: pointer; border-radius: 3px;
                 position: relative; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px;
                 line-height: 1.5; }
  ul.leaves li::before { content: ""; position: absolute; left: 0; top: 6px;
                          width: 8px; height: 8px; border-radius: 50%; }
  ul.leaves li.ts::before        { background: var(--green); }
  ul.leaves li.derived::before   { background: var(--blue); }
  ul.leaves li.collapsed::before { background: var(--grey); }
  ul.leaves li.root::before      { background: #555; }
  ul.leaves li.orphan::before    { background: var(--red); }
  ul.leaves li.selected { background: var(--selected); font-weight: 600; }
  ul.leaves li:hover { background: var(--hover); }
  ul.leaves li .kind-tag {
    display: inline-block; padding: 0 4px; border-radius: 2px; font-size: 9px;
    font-family: -apple-system, sans-serif; margin-left: 4px; color: white; opacity: 0.7;
  }
  ul.leaves li .kind-tag.abstract { background: var(--abstract); }
  ul.leaves li .kind-tag.physical { background: var(--physical); }
  .detail-pane h2 { margin: 0 0 6px 0; font-size: 17px; font-family: ui-monospace, Menlo, monospace; }
  .meta-grid { display: grid; grid-template-columns: 130px 1fr; gap: 4px 12px;
               padding: 10px 14px; background: var(--panel); border: 1px solid var(--border);
               border-radius: 4px; font-size: 12px; margin-bottom: 12px; }
  .meta-grid .k { color: #666; }
  .meta-grid .v { font-family: ui-monospace, Menlo, monospace; }
  .vars-table { width: 100%; border-collapse: collapse; background: var(--panel);
                border: 1px solid var(--border); border-radius: 4px; overflow: hidden;
                font-size: 12px; }
  .vars-table th, .vars-table td { padding: 5px 8px; text-align: left; border-bottom: 1px solid var(--border); }
  .vars-table th { background: #f0f0eb; font-weight: 600; }
  .vars-table tr:last-child td { border-bottom: none; }
  .vars-table td.var-id { font-family: ui-monospace, Menlo, monospace; font-size: 11px; }
  .vars-table tr.has-ts { background: #f0fae8; }
  .vars-table tr.clickable { cursor: pointer; }
  .vars-table tr.clickable:hover { background: #e6f0ff; }
  #chart { width: 100%; height: 40vh; margin-top: 12px; }
  .placeholder { color: #999; padding: 28px; text-align: center; font-style: italic; }
  h3.section { font-size: 13px; margin: 12px 0 6px 0; color: #555; }
</style>
</head>
<body>
<header>
  <h1>oil_network &middot; hierarchy explorer</h1>
  <div class="audit">
    <b>__N_NODES__</b> nodes &middot;
    physical <b>__N_PHYSICAL__</b> &middot;
    abstract <b>__N_ABSTRACT__</b> &middot;
    <span style="color:var(--green)">●</span> authoritative <b>__N_WITH_DATA__</b> &middot;
    <span style="color:var(--blue)">●</span> derived <b>__N_DERIVED__</b> &middot;
    <span style="color:var(--grey)">●</span> collapsed <b>__N_COLLAPSED__</b> &middot;
    <span style="color:var(--red)">●</span> orphans <b>__N_ORPHANS__</b>
  </div>
</header>
<div class="layout">
  <div class="tree-pane" id="tree"></div>
  <div class="detail-pane">
    <div id="detail"><div class="placeholder">Click a node in the tree to inspect.</div></div>
    <div id="chart"></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const NODES = DATA.nodes;
const ROOTS = DATA.roots;
const TS = DATA.ts;

function makeNodeElement(nid, depth) {
  const n = NODES[nid];
  if (!n) return null;
  const children = n.children;
  const hasChildren = children.length > 0;
  const subtypeBadge = n.kind === "abstract" ? "abs" : "phy";

  const labelHTML =
    `<span class="badge ${n.status}"></span>` +
    `${nid}` +
    `<span class="count">${hasChildren ? "(" + children.length + ")" : ""}</span>` +
    `<span class="kind-tag ${n.kind}">${subtypeBadge}</span>`;

  if (hasChildren) {
    const det = document.createElement("details");
    det.dataset.nodeId = nid;
    if (depth < 2) det.open = true;
    const sum = document.createElement("summary");
    sum.innerHTML = labelHTML;
    sum.dataset.nodeId = nid;
    sum.onclick = (e) => {
      // allow toggle; also select
      selectNode(nid, sum);
    };
    det.appendChild(sum);
    for (const child of children) {
      const ce = makeNodeElement(child, depth + 1);
      if (ce) det.appendChild(ce);
    }
    return det;
  } else {
    // leaf
    const ul = document.createElement("ul");
    ul.className = "leaves";
    const li = document.createElement("li");
    li.className = n.status;
    li.innerHTML = `${nid}<span class="kind-tag ${n.kind}">${subtypeBadge}</span>`;
    li.dataset.nodeId = nid;
    li.onclick = () => selectNode(nid, li);
    ul.appendChild(li);
    return ul;
  }
}

function buildTree() {
  const treeDiv = document.getElementById("tree");
  treeDiv.innerHTML = "";
  for (const r of ROOTS) {
    const el = makeNodeElement(r, 0);
    if (el) treeDiv.appendChild(el);
  }
}

function statusLabel(s) {
  switch (s) {
    case "ts":        return '<span style="color:var(--green)">●</span> authoritative — has own TS data';
    case "derived":   return '<span style="color:var(--blue)">●</span> derived — computed from constituents (formula-bound)';
    case "collapsed": return '<span style="color:var(--grey)">●</span> collapsed — no data; only zero/latent formulas';
    case "root":      return '<span style="color:#555">●</span> root — top-level abstract node';
    case "orphan":    return '<span style="color:var(--red)">●</span> orphan — no data and no parent';
    default:          return s;
  }
}

let _selected = null;
function selectNode(nid, el) {
  if (_selected) _selected.classList.remove("selected");
  el.classList.add("selected");
  _selected = el;

  const n = NODES[nid];
  if (!n) return;
  const det = document.getElementById("detail");

  // Build variables table
  let varsRows = "";
  for (const v of n.variables) {
    const isTS = !!v.timeseries_id;
    const tsBadge = isTS ? "✓" : "—";
    const tsLink = isTS ? `<a href="#" data-ts="${v.timeseries_id}">${v.timeseries_id}</a>` : "";
    const formula = v.formula ? `<code>${v.formula}</code>` : "";
    const rel = v.related_node ? ` → ${v.related_node}` : "";
    varsRows += `<tr class="${isTS ? 'has-ts clickable' : ''}" data-ts="${v.timeseries_id || ''}">
      <td class="var-id">${v.variable_type}${rel}</td>
      <td>${tsBadge}</td>
      <td>${tsLink}</td>
      <td>${formula}</td>
    </tr>`;
  }

  let parentInfo = n.parent
    ? `<span class="v">${n.parent} <span style="color:#999;font-size:11px">(via ${n.parent_source})</span></span>`
    : `<span class="v"><i>root</i></span>`;

  det.innerHTML = `
    <h2>${nid}</h2>
    <div class="meta-grid">
      <div class="k">name</div>           <div class="v">${n.name || ""}</div>
      <div class="k">kind</div>           <div class="v">${n.kind}</div>
      <div class="k">class / subtype</div><div class="v">${n.node_class} / ${n.node_subtype}</div>
      <div class="k">parent</div>         ${parentInfo.replace(/^<span class="v">/, '<div class="v">').replace(/<\\/span>$/, '</div>')}
      <div class="k">children</div>       <div class="v">${n.children.length}</div>
      <div class="k">status</div>         <div class="v">${statusLabel(n.status)}</div>
      <div class="k">PADD / state</div>   <div class="v">${n.padd || ''} ${n.state ? '/ ' + n.state : ''}</div>
    </div>
    <h3 class="section">Variables (${n.variables.length})</h3>
    <table class="vars-table">
      <thead><tr><th>type → related</th><th>TS?</th><th>timeseries_id</th><th>formula</th></tr></thead>
      <tbody>${varsRows || '<tr><td colspan="4" style="color:#999;padding:10px">no variables</td></tr>'}</tbody>
    </table>
  `;

  // Wire TS row clicks to chart
  det.querySelectorAll("tr.clickable").forEach(tr => {
    tr.onclick = () => plotTS(tr.dataset.ts);
  });

  document.getElementById("chart").innerHTML = "";
}

function plotTS(ts_id) {
  if (!ts_id || !TS[ts_id]) {
    document.getElementById("chart").innerHTML =
      '<div class="placeholder">No time series data available.</div>';
    return;
  }
  const ts = TS[ts_id];
  const x = ts.points.map(p => p[0]);
  const y = ts.points.map(p => p[1]);
  Plotly.newPlot("chart", [{
    x, y, type: "scatter", mode: "lines+markers",
    line: { color: "#1f77b4", width: 1.5 },
    marker: { size: 4 },
    name: ts_id
  }], {
    title: `${ts.name || ts_id}<br><sub>${ts_id} (${ts.unit})</sub>`,
    xaxis: { title: "date", type: "date" },
    yaxis: { title: ts.unit },
    margin: { l: 60, r: 30, t: 70, b: 50 },
    hovermode: "x unified",
  }, { responsive: true });
}

buildTree();
</script>
</body>
</html>
"""


def main():
    print("Querying DB...")
    assets, variables, ts_data, ts_meta, node_status_map = fetch()
    print(f"  assets: {len(assets)}, variables: {len(variables)}, ts with data: {len(ts_data)}")

    parent_map = build_parent_map(assets, variables)
    print(f"  parent links: {len(parent_map)}")

    payload = build_payload(assets, variables, ts_data, ts_meta, parent_map, node_status_map)
    aud = payload["audit"]
    print(f"  audit: {aud['n_nodes']} nodes, "
          f"ts={aud['n_with_data']}, derived={aud['n_derived']}, "
          f"collapsed={aud['n_collapsed']}, orphans={aud['n_orphans']}, "
          f"roots={len(aud['roots'])}")

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    for k in ("n_nodes", "n_physical", "n_abstract", "n_with_data", "n_derived",
              "n_collapsed", "n_orphans"):
        html = html.replace(f"__{k.upper()}__", str(aud[k]))

    HTML_OUT.write_text(html, encoding="utf-8")
    size_kb = HTML_OUT.stat().st_size // 1024
    print(f"Wrote {HTML_OUT}  ({size_kb} KB)")

    # Also dump orphan list for the handover
    orphans = [(nid, payload["nodes"][nid]["node_subtype"])
               for nid in payload["nodes"]
               if payload["nodes"][nid]["status"] == "orphan"]
    print(f"\nOrphans ({len(orphans)}):")
    by_subtype = defaultdict(int)
    for _nid, sub in orphans:
        by_subtype[sub] += 1
    for sub, n in sorted(by_subtype.items(), key=lambda x: -x[1]):
        print(f"  {sub:30s}  {n}")


if __name__ == "__main__":
    main()
