"""LIVE node-routes explorer (per-click DB query).

Run this script to start a tiny local HTTP server. It serves:

  GET /            -> the explorer HTML (UI only, no routes embedded)
  GET /nodes       -> {id: {name, subtype, state, padd}, ...} for the dropdown
  GET /routes?o=X  -> downstream routes starting at X (queries v_node_routes)
  GET /routes?d=X  -> upstream routes ending at X
  GET /routes?through=X  -> any route passing through X

The HTML calls /routes on each dropdown change. Compare to
`make_node_routes.py` (eager: routes embedded once at generation time).

Usage:
    python code/serve_node_routes.py
    -> http://localhost:8765/
"""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
PORT = 8765


def db_query(sql, params=()):
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_nodes():
    rows = db_query("""
        SELECT a.asset_id, a.name, a.node_subtype, l.state, l.padd
        FROM oil_network.assets a
        LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
        WHERE a.kind = 'physical'
        ORDER BY a.asset_id
    """)
    return {r[0]: {"name": r[1] or r[0], "subtype": r[2], "state": r[3], "padd": r[4]} for r in rows}


def get_routes(origin=None, destination=None, through=None):
    if origin:
        rows = db_query("SELECT origin, destination, hops, path FROM oil_network.v_node_routes WHERE origin = %s", (origin,))
    elif destination:
        rows = db_query("SELECT origin, destination, hops, path FROM oil_network.v_node_routes WHERE destination = %s", (destination,))
    elif through:
        rows = db_query("SELECT origin, destination, hops, path FROM oil_network.v_node_routes WHERE %s = ANY(path)", (through,))
    else:
        rows = []
    return [{"o": r[0], "d": r[1], "h": r[2], "p": r[3]} for r in rows]


HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>oil_network — node routes (live)</title>
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
  .loading { color:#888; font-style:italic; }
</style></head><body>
<header><h1>oil_network — node routes (live, per-click query)</h1><div class="m" id="hmeta">loading...</div></header>
<div class="controls">
  <input id="q" placeholder="filter physical nodes...">
  <select id="sel"></select>
</div>
<div class="container">
  <div class="panel up"><h2>Routes TO this node (upstream)</h2><div class="stats" id="us"></div><ul class="tree" id="ut"></ul></div>
  <div class="panel dn"><h2>Routes FROM this node (downstream)</h2><div class="stats" id="ds"></div><ul class="tree" id="dt"></ul></div>
</div>
<script>
let NODES = {};
const sel = document.getElementById('sel'), q = document.getElementById('q');

async function init() {
  const r = await fetch('/nodes'); NODES = await r.json();
  document.getElementById('hmeta').textContent = `${Object.keys(NODES).length} physical nodes · routes queried per-click from v_node_routes`;
  rebuild(''); render();
}
function rebuild(f) {
  f = (f||'').toLowerCase(); sel.innerHTML = '';
  const ids = Object.keys(NODES).sort((a,b) => NODES[a].name.localeCompare(NODES[b].name));
  for (const id of ids) {
    const n = NODES[id];
    const blob = (id + ' ' + n.name + ' ' + (n.subtype||'') + ' ' + (n.state||'') + ' ' + (n.padd||'')).toLowerCase();
    if (f && !blob.includes(f)) continue;
    const o = document.createElement('option');
    o.value = id; o.textContent = `${n.name}  [${id}]` + (n.state?` · ${n.state}`:'') + (n.padd?` · ${n.padd}`:'');
    sel.appendChild(o);
  }
}
q.oninput = () => { rebuild(q.value); render(); }; sel.onchange = render;

function trie(paths) {
  const root = { children: {} };
  for (const p of paths) { let cur = root; for (const id of p) { if (!cur.children[id]) cur.children[id] = { children:{} }; cur = cur.children[id]; } }
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

async function render() {
  const node = sel.value; if (!node) return;
  document.getElementById('us').innerHTML = '<span class="loading">querying...</span>';
  document.getElementById('ds').innerHTML = '<span class="loading">querying...</span>';
  document.getElementById('ut').innerHTML = '';
  document.getElementById('dt').innerHTML = '';
  const [dnRes, upRes] = await Promise.all([fetch('/routes?o=' + encodeURIComponent(node)), fetch('/routes?d=' + encodeURIComponent(node))]);
  const dn = (await dnRes.json()).map(r => r.p);
  const up = (await upRes.json()).map(r => [...r.p].reverse());
  document.getElementById('us').textContent = `${up.length} routes ending here (fetched live)`;
  document.getElementById('ds').textContent = `${dn.length} routes starting here (fetched live)`;
  if (up.length) renderTrie(trie(up), document.getElementById('ut'), node); else document.getElementById('ut').innerHTML = '<li class="empty">no upstream routes</li>';
  if (dn.length) renderTrie(trie(dn), document.getElementById('dt'), node); else document.getElementById('dt').innerHTML = '<li class="empty">no downstream routes</li>';
}
init();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/" or u.path == "/index.html":
            self._send_html(HTML)
        elif u.path == "/nodes":
            self._send_json(get_nodes())
        elif u.path == "/routes":
            q = parse_qs(u.query)
            self._send_json(get_routes(
                origin=(q.get("o") or [None])[0],
                destination=(q.get("d") or [None])[0],
                through=(q.get("through") or [None])[0],
            ))
        else:
            self.send_error(404)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"oil_network node-routes server on http://127.0.0.1:{PORT}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
