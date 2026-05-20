"""Commodities hierarchy explorer.

Output: oil_network_commodities.html

Renders the `oil_network.commodities` + `oil_network.commodity_hierarchy`
tables as a collapsible tree. Click a node in the tree to see its full
metadata (description, sweet/sour class, density class, API gravity range,
sulfur range, region, typical basin) in the right-hand panel.

Stage-2 first explorer aimed at the grade-decomposition workstream: a quick
visual sanity check that every grade is correctly nested under `crude` and
that the metadata is coherent before we wire variables to grades.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

import psycopg2

from paths import HTML_DIR

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
HTML_OUT = HTML_DIR / "oil_network_commodities.html"


SWEET_SOUR_COLOR = {
    "sweet": "#2ca02c",
    "medium_sour": "#ff7f0e",
    "sour": "#d62728",
    None: "#888888",
}

DENSITY_LABEL = {
    "very_light": "Very light",
    "light": "Light",
    "medium": "Medium",
    "heavy": "Heavy",
    None: "—",
}


def fetch_data():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT commodity, description, sweet_sour, density_class,
                   api_gravity_min, api_gravity_max,
                   sulfur_pct_min, sulfur_pct_max,
                   region, typical_basin
            FROM oil_network.commodities
            ORDER BY commodity
        """)
        commodities = {
            r[0]: {
                "commodity": r[0],
                "description": r[1] or "",
                "sweet_sour": r[2],
                "density_class": r[3],
                "api_min": float(r[4]) if r[4] is not None else None,
                "api_max": float(r[5]) if r[5] is not None else None,
                "sulfur_min": float(r[6]) if r[6] is not None else None,
                "sulfur_max": float(r[7]) if r[7] is not None else None,
                "region": r[8],
                "typical_basin": r[9],
                "children": [],
            }
            for r in cur.fetchall()
        }
        cur.execute("""
            SELECT parent_commodity, child_commodity
            FROM oil_network.commodity_hierarchy
            ORDER BY parent_commodity, child_commodity
        """)
        for parent, child in cur.fetchall():
            if parent in commodities and child in commodities:
                commodities[parent]["children"].append(child)
        # Compute root(s): commodities never appearing as a child
        children_set = set()
        cur.execute("SELECT child_commodity FROM oil_network.commodity_hierarchy")
        children_set = {r[0] for r in cur.fetchall()}
        roots = sorted(c for c in commodities if c not in children_set)
    return commodities, roots


def build_tree(commodities: dict, roots: list[str]):
    """Recursive tree builder. Returns a list of root dicts with nested children."""

    def expand(name: str):
        node = dict(commodities[name])
        node["children"] = [expand(c) for c in sorted(node["children"])]
        return node

    return [expand(r) for r in roots]


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>oil_network — commodities hierarchy</title>
<style>
  :root {
    --bg: #fafafa;
    --card: #ffffff;
    --border: #e5e5e5;
    --muted: #888;
    --text: #222;
    --accent: #1f77b4;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         background: var(--bg); color: var(--text); }
  header { background: #fff; border-bottom: 1px solid var(--border); padding: 14px 24px; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .container { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 24px; max-width: 1400px; margin: 0 auto; }
  @media (max-width: 900px) { .container { grid-template-columns: 1fr; } }

  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 16px; }
  .panel h2 { margin: 0 0 12px 0; font-size: 14px; font-weight: 600; color: var(--muted);
              text-transform: uppercase; letter-spacing: 0.04em; }

  ul.tree, ul.tree ul { list-style: none; padding-left: 0; margin: 0; }
  ul.tree ul { padding-left: 18px; border-left: 1px dashed var(--border); margin-left: 6px; }
  ul.tree li { padding: 2px 0; }
  .twisty { display: inline-block; width: 14px; cursor: pointer; user-select: none; color: var(--muted); font-size: 10px; }
  .twisty.leaf { color: transparent; cursor: default; }
  .node-label { cursor: pointer; padding: 2px 6px; border-radius: 3px; display: inline-block; font-family: "SF Mono", Consolas, Monaco, monospace; font-size: 13px; }
  .node-label:hover { background: #f0f7ff; }
  .node-label.selected { background: var(--accent); color: #fff; }
  .sweetsour-chip { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }

  .detail { font-size: 13px; line-height: 1.45; }
  .detail .commodity-id { font-family: "SF Mono", Consolas, Monaco, monospace; font-weight: 600; font-size: 16px; }
  .detail .description { color: var(--text); margin: 8px 0 14px; }
  .detail table { width: 100%; border-collapse: collapse; }
  .detail td { padding: 4px 0; }
  .detail td:first-child { color: var(--muted); width: 38%; }
  .detail .placeholder { color: var(--muted); }
  .detail .children-count { color: var(--muted); font-size: 12px; margin-top: 10px; }
</style>
</head>
<body>
<header>
  <h1>oil_network — commodities hierarchy</h1>
  <div class="meta">__META__</div>
</header>
<div class="container">
  <div class="panel">
    <h2>Tree</h2>
    <ul class="tree" id="tree"></ul>
  </div>
  <div class="panel">
    <h2>Details</h2>
    <div class="detail" id="detail"><span class="placeholder">Click a commodity in the tree.</span></div>
  </div>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const SWEET_SOUR_COLOR = __SWEET_SOUR_COLOR__;
const DENSITY_LABEL = __DENSITY_LABEL__;

function fmtRange(lo, hi, suffix) {
  if (lo == null && hi == null) return '<span class="placeholder">—</span>';
  if (lo == null) return '≤ ' + hi + suffix;
  if (hi == null) return '≥ ' + lo + suffix;
  return lo + '–' + hi + suffix;
}

function renderTree(nodes, parentEl) {
  for (const n of nodes) {
    const li = document.createElement('li');
    const hasChildren = n.children && n.children.length > 0;

    const twisty = document.createElement('span');
    twisty.className = 'twisty' + (hasChildren ? '' : ' leaf');
    twisty.textContent = hasChildren ? '▾' : '·';

    const chip = document.createElement('span');
    chip.className = 'sweetsour-chip';
    chip.style.background = SWEET_SOUR_COLOR[n.sweet_sour] || SWEET_SOUR_COLOR[null];

    const label = document.createElement('span');
    label.className = 'node-label';
    label.dataset.commodity = n.commodity;
    label.textContent = n.commodity;
    if (hasChildren) {
      label.textContent += ' (' + n.children.length + ')';
    }
    label.onclick = () => showDetail(n);

    li.appendChild(twisty);
    li.appendChild(chip);
    li.appendChild(label);

    if (hasChildren) {
      const sublist = document.createElement('ul');
      li.appendChild(sublist);
      renderTree(n.children, sublist);
      twisty.onclick = () => {
        const open = sublist.style.display !== 'none';
        sublist.style.display = open ? 'none' : '';
        twisty.textContent = open ? '▸' : '▾';
      };
    }

    parentEl.appendChild(li);
  }
}

function showDetail(n) {
  document.querySelectorAll('.node-label.selected').forEach(el => el.classList.remove('selected'));
  document.querySelector(`.node-label[data-commodity="${n.commodity}"]`).classList.add('selected');

  const d = document.getElementById('detail');
  const rows = [
    ['Description', n.description || '<span class="placeholder">—</span>'],
    ['Sweet / sour', n.sweet_sour ? `<span class="sweetsour-chip" style="background:${SWEET_SOUR_COLOR[n.sweet_sour] || SWEET_SOUR_COLOR[null]}"></span>${n.sweet_sour}` : '<span class="placeholder">—</span>'],
    ['Density class', DENSITY_LABEL[n.density_class] || '<span class="placeholder">—</span>'],
    ['API gravity (°)', fmtRange(n.api_min, n.api_max, '')],
    ['Sulfur (%)', fmtRange(n.sulfur_min, n.sulfur_max, '')],
    ['Region', n.region || '<span class="placeholder">—</span>'],
    ['Typical basin', n.typical_basin || '<span class="placeholder">—</span>'],
  ];
  const childRow = n.children && n.children.length > 0
    ? `<div class="children-count">${n.children.length} child commodit${n.children.length === 1 ? 'y' : 'ies'}: ${n.children.map(c => c.commodity).join(', ')}</div>`
    : '<div class="children-count">Leaf commodity (no children)</div>';

  d.innerHTML = `
    <div class="commodity-id">${n.commodity}</div>
    <div class="description">${n.description}</div>
    <table><tbody>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')}</tbody></table>
    ${childRow}
  `;
}

renderTree(DATA, document.getElementById('tree'));
</script>
</body>
</html>
"""


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    commodities, roots = fetch_data()
    tree = build_tree(commodities, roots)

    n_commod = len(commodities)
    n_edges = sum(len(c["children"]) for c in commodities.values())
    n_roots = len(roots)
    meta = (
        f"{n_commod} commodities · {n_edges} hierarchy edges · {n_roots} root(s) · "
        f"generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    html = (HTML_TEMPLATE
        .replace("__META__", meta)
        .replace("__DATA__", json.dumps(tree))
        .replace("__SWEET_SOUR_COLOR__", json.dumps(SWEET_SOUR_COLOR))
        .replace("__DENSITY_LABEL__", json.dumps(DENSITY_LABEL))
    )

    HTML_OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size:,} bytes)")
    print(f"  {n_commod} commodities, {n_edges} edges, {n_roots} root(s)")


if __name__ == "__main__":
    main()
