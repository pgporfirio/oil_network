"""EAGER node-routes explorer.

Generates `outputs/html/oil_network_node_routes.html` — a self-contained
HTML that embeds all rows of `oil_network.v_node_routes` and renders the
upstream / downstream trees client-side from that payload.

For the live (per-click query) variant see `serve_node_routes.py`.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import psycopg2
from paths import HTML_DIR

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
HTML_OUT = HTML_DIR / "oil_network_node_routes.html"


def fetch():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.name, a.node_subtype, l.state, l.padd
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.kind = 'physical'
            ORDER BY a.asset_id
        """)
        nodes = {r[0]: {"name": r[1] or r[0], "subtype": r[2], "state": r[3], "padd": r[4]} for r in cur.fetchall()}
        cur.execute("SELECT origin, destination, hops, path FROM oil_network.v_node_routes")
        routes = [{"o": r[0], "d": r[1], "h": r[2], "p": r[3]} for r in cur.fetchall()]
    return nodes, routes


HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>oil_network — node routes (eager)</title>
<style>
  body { margin:0; font-family:-apple-system,Segoe UI,sans-serif; background:#fafafa; color:#222; font-size:13px; }
  header { background:#fff; border-bottom:1px solid #e5e5e5; padding:12px 24px; }
  header h1 { margin:0; font-size:16px; } header .m { color:#888; font-size:11px; }
  .controls { background:#fff; border-bottom:1px solid #e5e5e5; padding:10px 24px; display:flex; gap:10px; align-items:center; }
  .controls input { padding:5px 8px; border:1px solid #ccc; border-radius:4px; width:220px; font:inherit; }
  .controls select { padding:5px 8px; border:1px solid #ccc; border-radius:4px; font:inherit; min-width:420px; }
  .container { display:grid; grid-template-columns:1fr 1fr; gap:14px; padding:14px 24px; max-width:1700px; margin:0 auto; }
  .panel { background:#fff; border:1px solid #e5e5e5; border-radius:5px; padding:14px; }
  .panel h2 { margin:0 0 8px 0; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .panel.up h2 { color:#ff7f0e; } .panel.dn h2 { color:#2ca02c; }
  .stats { color:#888; font-size:11px; margin-bottom:8px; }
  ul.tree, ul.tree ul { list-style:none; padding-left:0; margin:0; }
  ul.tree ul { padding-left:16px; border-left:1px dashed #e5e5e5; margin-left:5px; }
  ul.tree li { padding:1px 0; }
  .t { display:inline-block; width:12px; cursor:pointer; color:#888; font-size:9px; }
  .t.leaf { color:transparent; cursor:default; }
  .n { padding:1px 5px; border-radius:3px; font-family:Consolas,monospace; font-size:12px; }
  .n.self { background:#1f77b4; color:#fff; }
  .meta { color:#888; font-size:10px; margin-left:3px; }
  .empty { color:#888; font-style:italic; }
</style></head><body>
<header><h1>oil_network — node routes (eager)</h1><div class="m">__META__</div></header>
<div class="controls">
  <input id="q" placeholder="filter physical nodes...">
  <select id="sel"></select>
</div>
<div class="container">
  <div class="panel up"><h2>Routes TO this node (upstream)</h2><div class="stats" id="us"></div><ul class="tree" id="ut"></ul></div>
  <div class="panel dn"><h2>Routes FROM this node (downstream)</h2><div class="stats" id="ds"></div><ul class="tree" id="dt"></ul></div>
</div>
<script id="d" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('d').textContent);
const NODES = D.nodes, ROUTES = D.routes;
const ids = Object.keys(NODES).sort((a,b) => NODES[a].name.localeCompare(NODES[b].name));
const sel = document.getElementById('sel'), q = document.getElementById('q');

function rebuild(f) {
  f = (f||'').toLowerCase(); sel.innerHTML = '';
  for (const id of ids) {
    const n = NODES[id];
    const blob = (id + ' ' + n.name + ' ' + (n.subtype||'') + ' ' + (n.state||'') + ' ' + (n.padd||'')).toLowerCase();
    if (f && !blob.includes(f)) continue;
    const o = document.createElement('option');
    o.value = id; o.textContent = `${n.name}  [${id}]` + (n.state?` · ${n.state}`:'') + (n.padd?` · ${n.padd}`:'');
    sel.appendChild(o);
  }
}
rebuild(''); q.oninput = () => { rebuild(q.value); render(); }; sel.onchange = render;

// Build trie from a list of node-id arrays. Each path is consumed in order.
function trie(paths) {
  const root = { children: {} };
  for (const p of paths) {
    let cur = root;
    for (const id of p) {
      if (!cur.children[id]) cur.children[id] = { children: {} };
      cur = cur.children[id];
    }
  }
  return root;
}

function renderTrie(node, parent, selfId) {
  const keys = Object.keys(node.children).sort();
  for (const k of keys) {
    const child = node.children[k];
    const li = document.createElement('li');
    const hasKids = Object.keys(child.children).length > 0;
    const t = document.createElement('span'); t.className = 't' + (hasKids?'':' leaf'); t.textContent = hasKids?'▾':'·';
    const n = NODES[k]; const isSelf = (k === selfId);
    const lbl = document.createElement('span'); lbl.className = 'n' + (isSelf?' self':''); lbl.textContent = k;
    const meta = document.createElement('span'); meta.className = 'meta';
    meta.textContent = ' ' + (n?.subtype||'') + (n?.state?` ${n.state}`:'') + (n?.padd?`·${n.padd}`:'');
    li.appendChild(t); li.appendChild(lbl); li.appendChild(meta);
    if (hasKids) {
      const ul = document.createElement('ul'); li.appendChild(ul);
      renderTrie(child, ul, selfId);
      t.onclick = () => { const open = ul.style.display !== 'none'; ul.style.display = open?'none':''; t.textContent = open?'▸':'▾'; };
    }
    parent.appendChild(li);
  }
}

function render() {
  const node = sel.value; if (!node) return;
  // Downstream: routes where origin == node, walk path forward from index 0
  const dn = ROUTES.filter(r => r.o === node).map(r => r.p);
  // Upstream: routes where destination == node, walk path BACKWARD from end
  const up = ROUTES.filter(r => r.d === node).map(r => [...r.p].reverse());

  document.getElementById('us').textContent = `${up.length} routes ending here`;
  document.getElementById('ds').textContent = `${dn.length} routes starting here`;
  const ut = document.getElementById('ut'); ut.innerHTML = '';
  const dt = document.getElementById('dt'); dt.innerHTML = '';
  if (up.length) renderTrie(trie(up), ut, node); else ut.innerHTML = '<li class="empty">no upstream routes</li>';
  if (dn.length) renderTrie(trie(dn), dt, node); else dt.innerHTML = '<li class="empty">no downstream routes</li>';
}
render();
</script></body></html>
"""


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    nodes, routes = fetch()
    meta = (f"{len(nodes)} physical nodes · {len(routes):,} routes (from v_node_routes) · eager load · "
            f"generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    html = HTML.replace("__META__", meta).replace("__DATA__", json.dumps({"nodes": nodes, "routes": routes}))
    HTML_OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
