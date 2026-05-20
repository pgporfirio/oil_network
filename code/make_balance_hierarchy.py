"""Generate oil_network_balance_hierarchy.html — eager / self-contained.

Same UI as serve_balance_hierarchy.py (port 8766), but the entire dataset
(both scenarios x all nodes x all dates x all (variable_type, commodity)
cells from scenario_resolved_values) is embedded in the page, so it opens
directly with no server.

For size, we compact: cells with value=0 and source='zero' (the structural
default) are omitted and the JS treats a missing cell as zero. Cells with
value=null go in as {v:null,s:'L'|'p'|'l'} only when actually present.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone

import psycopg2
from paths import HTML_DIR

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
HTML_OUT = HTML_DIR / "oil_network_balance_hierarchy.html"

# Compact source encoding (single char in payload)
SRC_CODE = {"observed": "o", "derived": "d", "zero": "z", "latent": "L",
            "partial": "p", "unresolved": "u"}
SRC_DECODE = {v: k for k, v in SRC_CODE.items()}


def fetch_payload():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT scenario_id FROM oil_network.scenarios ORDER BY scenario_id")
        scenarios = [r[0] for r in cur.fetchall()]

        cur.execute("""SELECT a.asset_id, a.name, a.node_subtype, l.state, l.padd
                       FROM oil_network.assets a
                       LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
                       WHERE a.kind = 'physical' ORDER BY a.asset_id""")
        nodes = {r[0]: {"name": r[1] or r[0], "subtype": r[2], "state": r[3], "padd": r[4]}
                 for r in cur.fetchall()}

        cur.execute("""SELECT DISTINCT observation_date FROM oil_network.scenario_resolved_values
                       ORDER BY observation_date""")
        dates = [r[0].isoformat() for r in cur.fetchall()]

        cur.execute("SELECT commodity, sweet_sour FROM oil_network.commodities ORDER BY commodity")
        commodities = [{"id": r[0], "sweet_sour": r[1]} for r in cur.fetchall()]

        cur.execute("SELECT parent_commodity, child_commodity FROM oil_network.commodity_hierarchy")
        hierarchy_edges = [list(r) for r in cur.fetchall()]

        # Aggregate scenario_resolved_values to (scenario, node, date, var_type, commodity).
        # For inflow/outflow this sums across related_node edges; for the others it's the
        # single row per (variable_id).
        print("[fetch] aggregating resolved values (this is the heavy step)...")
        cur.execute("""
            SELECT srv.scenario_id, v.node_id, srv.observation_date,
                   v.variable_type, v.commodity,
                   SUM(srv.value) AS sum_value,
                   bool_or(srv.value IS NULL) AS any_null,
                   array_agg(DISTINCT srv.source) AS sources,
                   COUNT(*) AS n_rows
            FROM oil_network.variables v
            JOIN oil_network.scenario_resolved_values srv ON srv.variable_id = v.variable_id
            JOIN oil_network.assets a ON a.asset_id = v.node_id
            WHERE a.kind = 'physical'
            GROUP BY srv.scenario_id, v.node_id, srv.observation_date,
                     v.variable_type, v.commodity
        """)
        rows = cur.fetchall()
    print(f"[fetch]   {len(rows):,} aggregated cells")

    # Compact nested structure: payload[scenario_idx][node_id][date_idx][var_type][commodity] = cell
    # Cells omitted entirely if source is exclusively 'zero' AND value is 0 (saves ~half the size).
    sce_idx = {s: i for i, s in enumerate(scenarios)}
    date_idx = {d: i for i, d in enumerate(dates)}
    data = [{} for _ in scenarios]  # one dict per scenario
    n_kept = 0
    n_skipped_zero = 0
    for sc, node, d, vt, com, sv, any_null, sources, n_rows in rows:
        sv_f = float(sv) if sv is not None else None
        sources_clean = sorted(s for s in sources if s)
        # Skip pure zero defaults (saves a lot of space)
        is_zero_default = (sources_clean == ["zero"] and not any_null and sv_f == 0.0)
        if is_zero_default:
            n_skipped_zero += 1
            continue
        src_str = "".join(SRC_CODE.get(s, "?") for s in sources_clean)
        sc_data = data[sce_idx[sc]]
        node_data = sc_data.setdefault(node, {})
        date_data = node_data.setdefault(date_idx[d.isoformat()], {})
        vt_data = date_data.setdefault(vt, {})
        cell = {"s": src_str}
        cell["v"] = None if sv_f is None or (any_null and sv_f == 0) else sv_f
        # For inflow/outflow, n_edges = n_rows (each row is one edge)
        if vt in ("inflow", "outflow") and n_rows > 1:
            cell["n"] = n_rows
        vt_data[com] = cell
        n_kept += 1
    print(f"[fetch]   {n_kept:,} cells kept, {n_skipped_zero:,} zero-default cells omitted")

    return {
        "scenarios": scenarios,
        "nodes": nodes,
        "dates": dates,
        "commodities": commodities,
        "hierarchy_edges": hierarchy_edges,
        "data": data,
        "src_decode": SRC_DECODE,
    }


HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>oil_network - balance hierarchy</title>
<style>
  body { margin: 0; font-family: -apple-system, "Segoe UI", sans-serif; background: #fafafa; color: #222; font-size: 13px; }
  header { background: #fff; border-bottom: 1px solid #e5e5e5; padding: 12px 24px; }
  header h1 { margin: 0; font-size: 16px; } header .m { color: #888; font-size: 11px; }
  .controls { background: #fff; border-bottom: 1px solid #e5e5e5; padding: 10px 24px;
              display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .controls label { color: #888; font-size: 11.5px; }
  .controls select, .controls input { padding: 5px 8px; border: 1px solid #ccc; border-radius: 4px; font: inherit; }
  .controls select { min-width: 200px; } .controls input { width: 220px; }
  .container { padding: 16px 24px; max-width: 1300px; margin: 0 auto; }
  .panel { background: #fff; border: 1px solid #e5e5e5; border-radius: 6px; padding: 14px 18px; margin-bottom: 14px; }
  .panel h2 { margin: 0 0 6px 0; font-size: 13px; color: #555; text-transform: uppercase; letter-spacing: 0.04em; }
  .panel .meta { color: #888; font-size: 11px; margin-bottom: 8px; }
  .vt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
  .vt { background: #fff; border: 1px solid #e5e5e5; border-radius: 6px; padding: 14px 16px; }
  .vt h3 { margin: 0 0 8px 0; font-size: 13px; font-family: ui-monospace, Menlo, monospace; color: #333; }
  .vt h3 .total { float: right; color: #1f77b4; }
  ul.tree, ul.tree ul { list-style: none; padding-left: 0; margin: 0; }
  ul.tree ul { padding-left: 14px; border-left: 1px dashed #e5e5e5; margin-left: 4px; }
  ul.tree li { padding: 1px 0; display: flex; align-items: baseline; gap: 6px;
               font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .t { width: 10px; cursor: pointer; color: #aaa; font-size: 9px; user-select: none; }
  .t.leaf { color: transparent; cursor: default; }
  .commodity { flex: 1; }
  .value { color: #333; font-weight: 600; }
  .value.zero { color: #aaa; font-weight: 400; }
  .value.missing { color: #ccc; font-style: italic; font-weight: 400; }
  .source { color: #999; font-size: 10px; margin-left: 6px; }
  .empty { color: #999; font-style: italic; padding: 6px 0; }
</style></head><body>
<header><h1>oil_network - balance hierarchy</h1><div class="m">__META__</div></header>
<div class="controls">
  <label>Scenario</label><select id="scenario"></select>
  <label>Date</label><select id="date"></select>
  <label>Filter</label><input id="q" placeholder="filter physical nodes...">
  <label>Node</label><select id="node"></select>
</div>
<div class="container">
  <div class="panel">
    <h2 id="node-title">node</h2>
    <div class="meta" id="node-meta"></div>
    <div class="vt-grid" id="grid"></div>
  </div>
</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const PAYLOAD = JSON.parse(document.getElementById('payload').textContent);
const SCENARIOS = PAYLOAD.scenarios;
const NODES = PAYLOAD.nodes;
const DATES = PAYLOAD.dates;
const COMMODITIES = PAYLOAD.commodities;
const HEDGES = PAYLOAD.hierarchy_edges;
const DATA = PAYLOAD.data;            // per scenario index
const SRC_DECODE = PAYLOAD.src_decode;

// Build hierarchy {parent: [children]} and the list of roots
const HIERARCHY = {};
const isChild = new Set();
for (const [p, c] of HEDGES) { (HIERARCHY[p] || (HIERARCHY[p] = [])).push(c); isChild.add(c); }
const ROOTS = COMMODITIES.map(c => c.id).filter(id => !isChild.has(id));

// Date index for fast lookup
const DATE_IDX = {}; DATES.forEach((d, i) => DATE_IDX[d] = i);

const sceSel = document.getElementById('scenario');
for (const s of SCENARIOS) { const o = document.createElement('option'); o.value = s; o.textContent = s; sceSel.appendChild(o); }
const dateSel = document.getElementById('date');
for (const d of DATES) { const o = document.createElement('option'); o.value = d; o.textContent = d; dateSel.appendChild(o); }
dateSel.value = DATES.includes('2024-12-01') ? '2024-12-01' : DATES[DATES.length - 1];

const q = document.getElementById('q');
const nodeSel = document.getElementById('node');
function rebuildNodes(filter) {
  const f = (filter || '').toLowerCase();
  const preserved = nodeSel.value;
  nodeSel.innerHTML = '';
  for (const id of Object.keys(NODES).sort((a,b) => NODES[a].name.localeCompare(NODES[b].name))) {
    const n = NODES[id];
    const blob = (id + ' ' + n.name + ' ' + (n.subtype||'') + ' ' + (n.state||'') + ' ' + (n.padd||'')).toLowerCase();
    if (f && !blob.includes(f)) continue;
    const o = document.createElement('option');
    o.value = id;
    o.textContent = `${n.name}  [${id}]` + (n.state?` ${n.state}`:'') + (n.padd?`/${n.padd}`:'');
    nodeSel.appendChild(o);
  }
  if (preserved && Array.from(nodeSel.options).some(o => o.value === preserved)) nodeSel.value = preserved;
}
rebuildNodes('');
['scenario','date','node'].forEach(id => document.getElementById(id).addEventListener('change', render));
q.addEventListener('input', e => { rebuildNodes(e.target.value); render(); });

function fmt(v) {
  if (v === null || v === undefined) return null;
  if (Math.abs(v) < 0.005) return '0';
  return v.toLocaleString(undefined, {maximumFractionDigits: 1});
}

function lookupCell(sce_idx, node, date_idx, vt, com) {
  const sc = DATA[sce_idx]; if (!sc) return undefined;
  const n = sc[node]; if (!n) return undefined;
  const d = n[date_idx]; if (!d) return undefined;
  const v = d[vt]; if (!v) return undefined;
  return v[com];  // may be undefined => zero default
}

function renderTree(root, sce_idx, node, date_idx, vt, parentEl) {
  const li = document.createElement('li');
  const children = HIERARCHY[root] || [];
  const has = children.length > 0;
  const t = document.createElement('span'); t.className = 't' + (has?'':' leaf'); t.textContent = has?'▾':'·';
  const c = document.createElement('span'); c.className = 'commodity'; c.textContent = root;
  const v = document.createElement('span'); v.className = 'value';
  const cell = lookupCell(sce_idx, node, date_idx, vt, root);
  if (cell === undefined) { v.classList.add('zero'); v.textContent = '0'; }    // omitted = zero default
  else if (cell.v === null) { v.classList.add('missing'); v.textContent = '(latent)'; }
  else {
    const f = fmt(cell.v);
    if (f === '0') v.classList.add('zero');
    v.textContent = f;
  }
  const s = document.createElement('span'); s.className = 'source';
  if (cell && cell.s) {
    const srcs = [];
    for (const ch of cell.s) srcs.push(SRC_DECODE[ch] || ch);
    s.textContent = srcs.join(',') + (cell.n ? ` (${cell.n} edges)` : '');
  } else if (cell === undefined) {
    s.textContent = 'zero';
  }
  li.appendChild(t); li.appendChild(c); li.appendChild(v); li.appendChild(s);
  parentEl.appendChild(li);
  if (has) {
    const ul = document.createElement('ul'); li.appendChild(ul);
    for (const child of children.sort()) renderTree(child, sce_idx, node, date_idx, vt, ul);
    t.onclick = () => { const o = ul.style.display !== 'none'; ul.style.display = o?'none':''; t.textContent = o?'▸':'▾'; };
  }
}

function render() {
  const sc = sceSel.value; const node = nodeSel.value; const date = dateSel.value;
  if (!sc || !node || !date) return;
  const sce_idx = SCENARIOS.indexOf(sc);
  const date_idx = DATE_IDX[date];
  document.getElementById('node-title').textContent = node;
  const n = NODES[node];
  document.getElementById('node-meta').textContent =
    `${n.name} | ${n.subtype || ''} ${n.state ? '| ' + n.state : ''} ${n.padd ? '| ' + n.padd : ''} | scenario=${sc} date=${date}`;
  const grid = document.getElementById('grid'); grid.innerHTML = '';
  for (const vt of ['production','consumption','inventory','balancing_item','inflow','outflow']) {
    const tile = document.createElement('div'); tile.className = 'vt';
    const h = document.createElement('h3');
    const top = lookupCell(sce_idx, node, date_idx, vt, 'crude');
    const topVal = (top === undefined) ? '0' : fmt(top.v);
    h.innerHTML = `${vt}<span class="total">${topVal === null ? '(latent)' : topVal}</span>`;
    tile.appendChild(h);
    const ul = document.createElement('ul'); ul.className = 'tree';
    for (const root of ROOTS) renderTree(root, sce_idx, node, date_idx, vt, ul);
    tile.appendChild(ul);
  }
  render._currentGrid = render._currentGrid || null;
}
render();
</script></body></html>
"""


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    payload = fetch_payload()
    n_phys = len(payload["nodes"])
    n_dates = len(payload["dates"])
    n_sc = len(payload["scenarios"])
    n_cells = sum(
        len(date_data.get(vt, {}))
        for sc_data in payload["data"]
        for node_data in sc_data.values()
        for date_data in node_data.values()
        for vt in date_data
    )
    meta = (f"{n_sc} scenarios x {n_phys} physical nodes x {n_dates} dates - "
            f"{n_cells:,} embedded cells (zero-defaults omitted) - "
            f"generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    html = HTML.replace("__META__", meta).replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))
    HTML_OUT.write_text(html, encoding="utf-8")
    print(f"[write] {HTML_OUT}  ({HTML_OUT.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
