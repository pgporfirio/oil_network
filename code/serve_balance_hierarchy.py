"""Balance explorer with commodity-hierarchy drilldown (live).

Run:  python code/serve_balance_hierarchy.py
Then: http://127.0.0.1:8766/

Pick a scenario + node + date. The page shows the node's six variable types
(production, consumption, inventory, balancing_item, inflow, outflow). For
each, the values are arranged as a tree following the commodity_hierarchy:

  crude                        <- root, currently the only level populated
    wti                          <- (if/when grade variables get added)
      wti_midland
      wti_cushing
      wti_houston
    bakken_light
    eagle_ford_light
    ...

Each row shows the resolved value at the chosen date (or "-" if missing).
Today most rows below 'crude' are blank because the propagator hasn't yet
expanded per-grade variables. Once it does, they show up under the same
tree with no HTML change.

For inflow / outflow (relational), we aggregate (sum) over all
related_node edges per commodity. The per-edge breakdown is intentionally
deferred to oil_network_node_routes.html / node_neighbors.

Endpoints:
  GET /            -> HTML
  GET /init        -> scenarios, nodes, dates, hierarchy
  GET /data?scenario=X&node=Y&date=Z -> per-variable-type per-commodity values
"""
from __future__ import annotations
import json
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
PORT = 8766

VAR_TYPE_ORDER = ["production", "consumption", "inventory", "balancing_item", "inflow", "outflow"]


def db(sql, params=()):
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_init():
    scenarios = [r[0] for r in db("SELECT scenario_id FROM oil_network.scenarios ORDER BY scenario_id")]
    nodes = [{"id": r[0], "name": r[1] or r[0], "subtype": r[2], "state": r[3], "padd": r[4]}
             for r in db("""
                SELECT a.asset_id, a.name, a.node_subtype, l.state, l.padd
                FROM oil_network.assets a
                LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
                WHERE a.kind = 'physical' ORDER BY a.asset_id""")]
    dates = [r[0].isoformat() for r in db("""
        SELECT DISTINCT observation_date FROM oil_network.scenario_resolved_values
        ORDER BY observation_date""")]
    commodities = [{"id": r[0], "desc": r[1], "sweet_sour": r[2]} for r in db(
        "SELECT commodity, description, sweet_sour FROM oil_network.commodities ORDER BY commodity")]
    edges = [(r[0], r[1]) for r in db(
        "SELECT parent_commodity, child_commodity FROM oil_network.commodity_hierarchy")]
    return {"scenarios": scenarios, "nodes": nodes, "dates": dates,
            "commodities": commodities, "hierarchy_edges": edges}


def get_data(scenario, node, date):
    """Return {variable_type: {commodity: {value, source, n_edges}}}."""
    rows = db("""
        SELECT v.variable_type, v.commodity, srv.value, srv.source, srv.formula_used,
               v.related_node_id
        FROM oil_network.variables v
        JOIN oil_network.scenario_resolved_values srv ON srv.variable_id = v.variable_id
        WHERE v.node_id = %s AND srv.scenario_id = %s AND srv.observation_date = %s
    """, (node, scenario, date))
    out = defaultdict(lambda: defaultdict(lambda: {"sum": 0.0, "n": 0, "n_null": 0,
                                                    "sources": set(), "n_edges": 0}))
    for vtype, commod, value, source, fused, related in rows:
        cell = out[vtype][commod]
        if vtype in ("inflow", "outflow"):
            cell["n_edges"] += 1
        cell["n"] += 1
        if value is None:
            cell["n_null"] += 1
        else:
            cell["sum"] += float(value)
        if source:
            cell["sources"].add(source)
    # Serialise: pick a representative value (sum) and source set
    result = {}
    for vtype in VAR_TYPE_ORDER:
        if vtype not in out:
            continue
        result[vtype] = {}
        for commod, cell in out[vtype].items():
            result[vtype][commod] = {
                "value": cell["sum"] if cell["n_null"] < cell["n"] else None,
                "sources": sorted(cell["sources"]),
                "n_edges": cell["n_edges"] if vtype in ("inflow", "outflow") else None,
            }
    return result


HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>oil_network - balance hierarchy</title>
<style>
  body { margin: 0; font-family: -apple-system, "Segoe UI", sans-serif; background: #fafafa; color: #222; font-size: 13px; }
  header { background: #fff; border-bottom: 1px solid #e5e5e5; padding: 12px 24px; }
  header h1 { margin: 0; font-size: 16px; }
  .controls { background: #fff; border-bottom: 1px solid #e5e5e5; padding: 10px 24px;
              display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .controls label { color: #888; font-size: 11.5px; }
  .controls select, .controls input { padding: 5px 8px; border: 1px solid #ccc; border-radius: 4px; font: inherit; }
  .controls select { min-width: 200px; }
  .controls input { width: 220px; }
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
  ul.tree li { padding: 1px 0; display: flex; align-items: baseline; gap: 6px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .t { width: 10px; cursor: pointer; color: #aaa; font-size: 9px; user-select: none; }
  .t.leaf { color: transparent; cursor: default; }
  .commodity { flex: 1; }
  .value { color: #333; font-weight: 600; }
  .value.zero { color: #aaa; font-weight: 400; }
  .value.missing { color: #ccc; font-style: italic; font-weight: 400; }
  .source { color: #999; font-size: 10px; margin-left: 6px; }
  .empty { color: #999; font-style: italic; padding: 6px 0; }
</style></head><body>
<header><h1>oil_network - balance hierarchy</h1></header>
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
<script>
let INIT = null;
let HIERARCHY = null; // {parent: [children]}, includes synthetic root '__root__' -> [crude]
let ROOTS = [];       // [crude] (top-level commodities)

async function init() {
  INIT = await (await fetch('/init')).json();
  // Build hierarchy {parent: [children]}
  HIERARCHY = {};
  const isChild = new Set();
  for (const [p, c] of INIT.hierarchy_edges) {
    (HIERARCHY[p] || (HIERARCHY[p] = [])).push(c);
    isChild.add(c);
  }
  ROOTS = INIT.commodities.map(c => c.id).filter(id => !isChild.has(id));

  // Populate selectors
  const sceSel = document.getElementById('scenario');
  for (const s of INIT.scenarios) {
    const o = document.createElement('option'); o.value = s; o.textContent = s; sceSel.appendChild(o);
  }
  const dateSel = document.getElementById('date');
  for (const d of INIT.dates) {
    const o = document.createElement('option'); o.value = d; o.textContent = d; dateSel.appendChild(o);
  }
  // Default date: latest with a value (2024-12-01 if present, else last)
  const def = INIT.dates.includes('2024-12-01') ? '2024-12-01' : INIT.dates[INIT.dates.length - 1];
  dateSel.value = def;

  rebuildNodes('');
  ['scenario','date','node'].forEach(id => document.getElementById(id).addEventListener('change', render));
  document.getElementById('q').addEventListener('input', e => { rebuildNodes(e.target.value); render(); });
  render();
}

function rebuildNodes(filter) {
  const f = (filter || '').toLowerCase();
  const sel = document.getElementById('node');
  const preserved = sel.value;
  sel.innerHTML = '';
  for (const n of INIT.nodes) {
    const blob = (n.id + ' ' + n.name + ' ' + (n.subtype||'') + ' ' + (n.state||'') + ' ' + (n.padd||'')).toLowerCase();
    if (f && !blob.includes(f)) continue;
    const o = document.createElement('option');
    o.value = n.id;
    o.textContent = `${n.name}  [${n.id}]` + (n.state?` ${n.state}`:'') + (n.padd?`/${n.padd}`:'');
    sel.appendChild(o);
  }
  if (preserved && Array.from(sel.options).some(o => o.value === preserved)) sel.value = preserved;
}

function fmt(v) {
  if (v === null || v === undefined) return null;
  if (Math.abs(v) < 0.005) return '0';
  return v.toLocaleString(undefined, {maximumFractionDigits: 1});
}

function renderTree(root, dataByCommod, parentEl) {
  const li = document.createElement('li');
  const children = HIERARCHY[root] || [];
  const has = children.length > 0;
  const t = document.createElement('span'); t.className = 't' + (has?'':' leaf'); t.textContent = has?'▾':'·';
  const c = document.createElement('span'); c.className = 'commodity'; c.textContent = root;
  const v = document.createElement('span'); v.className = 'value';
  const cell = dataByCommod[root];
  if (cell === undefined) { v.classList.add('missing'); v.textContent = '-'; }
  else if (cell.value === null) { v.classList.add('missing'); v.textContent = '(latent)'; }
  else {
    const f = fmt(cell.value);
    if (f === '0') v.classList.add('zero');
    v.textContent = f;
  }
  const s = document.createElement('span'); s.className = 'source';
  if (cell && cell.sources && cell.sources.length) s.textContent = cell.sources.join(',');
  li.appendChild(t); li.appendChild(c); li.appendChild(v); li.appendChild(s);
  parentEl.appendChild(li);
  if (has) {
    const ul = document.createElement('ul'); li.appendChild(ul);
    for (const child of children.sort()) renderTree(child, dataByCommod, ul);
    t.onclick = () => { const o = ul.style.display !== 'none'; ul.style.display = o?'none':''; t.textContent = o?'▸':'▾'; };
  }
}

async function render() {
  const sc = document.getElementById('scenario').value;
  const node = document.getElementById('node').value;
  const date = document.getElementById('date').value;
  if (!sc || !node || !date) return;
  document.getElementById('node-title').textContent = node;
  const n = INIT.nodes.find(x => x.id === node);
  document.getElementById('node-meta').textContent =
    `${n.name} | ${n.subtype || ''} ${n.state ? '| ' + n.state : ''} ${n.padd ? '| ' + n.padd : ''} | scenario=${sc} date=${date}`;
  const grid = document.getElementById('grid'); grid.innerHTML = '';
  const data = await (await fetch(`/data?scenario=${encodeURIComponent(sc)}&node=${encodeURIComponent(node)}&date=${encodeURIComponent(date)}`)).json();
  const varTypes = ['production','consumption','inventory','balancing_item','inflow','outflow'];
  for (const vt of varTypes) {
    const tile = document.createElement('div'); tile.className = 'vt';
    const h = document.createElement('h3');
    const top = data[vt] && data[vt]['crude'];
    const topVal = top ? fmt(top.value) : '-';
    h.innerHTML = `${vt}<span class="total">${topVal}</span>`;
    tile.appendChild(h);
    if (!data[vt]) {
      const e = document.createElement('div'); e.className = 'empty'; e.textContent = 'no variables';
      tile.appendChild(e);
    } else {
      const ul = document.createElement('ul'); ul.className = 'tree';
      for (const root of ROOTS) renderTree(root, data[vt], ul);
      tile.appendChild(ul);
    }
    grid.appendChild(tile);
  }
}

init();
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, body, ct):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path in ("/", "/index.html"): self._send(HTML, "text/html; charset=utf-8")
        elif u.path == "/init": self._send(json.dumps(get_init()), "application/json")
        elif u.path == "/data":
            scenario = (q.get("scenario") or [""])[0]
            node = (q.get("node") or [""])[0]
            date = (q.get("date") or [""])[0]
            self._send(json.dumps(get_data(scenario, node, date)), "application/json")
        else: self.send_error(404)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"oil_network balance-hierarchy server on http://127.0.0.1:{PORT}/  (Ctrl-C to stop)")
    try: srv.serve_forever()
    except KeyboardInterrupt: srv.shutdown()


if __name__ == "__main__":
    main()
