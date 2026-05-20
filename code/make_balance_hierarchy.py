"""Generate oil_network_balance_hierarchy.html — same UI as
oil_network_balance_resolver.html but sourced from the
`crude_starter_with_grades` scenario.

Today both scenarios resolve identically (the grade variables haven't been
instantiated yet), so this page looks the same as the resolver UI. The
distinction matters once the propagator runs: per-grade variables land in
crude_starter_with_grades, and the P/C/I/O/B/S cells start showing the
sum-over-grades that closes back to crude.

Thin wrapper around make_balance_resolver_ui to avoid duplicating the
22 KB of HTML template + JS.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import date as _date

import psycopg2
import make_balance_ui as base
import make_balance_resolver_ui as resolver_ui
from network_graph import NetworkGraph
from paths import HTML_DIR
from render_utils import latest_run_id, write_html

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "crude_starter_with_grades"
HTML_OUT = HTML_DIR / "oil_network_balance_hierarchy.html"
VIEW_NAME = "balance_hierarchy"

LETTER_TO_VT = {"P": "production", "C": "consumption", "S": "inventory",
                "I": "inflow", "O": "outflow", "B": "balancing_item"}


def fetch_composition(kept_dates: list[_date]) -> tuple[dict, list, list]:
    """Return (composition, hierarchy_edges, commodities).
       composition[nid][letter][date_str][commodity] = value (summed over edges for I/O)."""
    kept = {d.isoformat() for d in kept_dates}
    comp: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # Include ALL nodes (physical AND abstract aggregates), so clicking the
        # 'bakken' aggregate, 'permian', a padd_view, etc. shows its commodity
        # breakdown too. Today aggregates only have crude variables, so they'll
        # show 'crude' populated and grades blank until per-grade aggregate
        # rollups are added.
        cur.execute("""
            SELECT v.node_id, v.variable_type, v.commodity, srv.observation_date, SUM(srv.value)
            FROM oil_network.variables v
            JOIN oil_network.scenario_resolved_values srv ON srv.variable_id = v.variable_id
            WHERE srv.scenario_id = %s
              AND v.variable_type IN ('production','consumption','inventory','balancing_item','inflow','outflow')
            GROUP BY v.node_id, v.variable_type, v.commodity, srv.observation_date
        """, (SCENARIO,))
        vt_to_letter = {v: k for k, v in LETTER_TO_VT.items()}
        for nid, vt, com, d, val in cur.fetchall():
            d_str = d.isoformat()
            if d_str not in kept or val is None:
                continue
            letter = vt_to_letter[vt]
            comp[nid][letter][d_str][com] = float(val)
        cur.execute("SELECT commodity FROM oil_network.commodities ORDER BY commodity")
        commodities = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT parent_commodity, child_commodity FROM oil_network.commodity_hierarchy")
        edges = [list(r) for r in cur.fetchall()]
    # Convert to plain dicts for JSON
    return ({n: {l: dict(dd) for l, dd in v.items()} for n, v in comp.items()},
            edges, commodities)


COMPOSITION_PATCH = r"""
<script id="composition-data" type="application/json">__COMPOSITION_JSON__</script>
<script>
(function() {
  const COMP = JSON.parse(document.getElementById('composition-data').textContent);
  const _hier = COMP._hierarchy_edges;
  // Build {parent:[children]} for the full hierarchy
  const H = {};
  for (const [p, c] of _hier) { (H[p] || (H[p] = [])).push(c); }
  // Composition is shown rooted at 'crude' (refined-product subtrees are intentionally
  // hidden here — that's a separate concern from per-grade balance).
  const ROOTS = ['crude'];

  // Add a Composition panel below the chart
  const pane = document.querySelector('.chart-pane');
  if (pane) {
    const div = document.createElement('div');
    div.id = 'composition-panel';
    div.style.cssText = 'margin-top:16px;border-top:1px solid #d0d0c8;padding-top:12px;font-size:12px;';
    div.innerHTML = '<h3 style="margin:0 0 8px 0;font-size:12px;color:#555;text-transform:uppercase;letter-spacing:.04em">Composition (commodity hierarchy)</h3><div id="composition-body" style="color:#999;font-style:italic">Click a cell to see the commodity breakdown.</div>';
    pane.appendChild(div);
  }

  function fmt(v) {
    if (v === null || v === undefined) return null;
    if (Math.abs(v) < 0.005) return '0';
    return v.toLocaleString(undefined, {maximumFractionDigits: 1});
  }

  function renderTree(root, byCommod, parent, depth) {
    const li = document.createElement('div');
    li.style.cssText = 'padding:1px 0;display:flex;gap:6px;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;padding-left:' + (depth * 14) + 'px;';
    const name = document.createElement('span'); name.style.flex = '1'; name.textContent = root;
    const val = document.createElement('span');
    const v = byCommod[root];
    if (v === undefined) { val.style.color = '#ccc'; val.textContent = '-'; }
    else { val.style.color = '#222'; val.style.fontWeight = '600'; val.textContent = fmt(v); }
    li.appendChild(name); li.appendChild(val);
    parent.appendChild(li);
    for (const child of (H[root] || []).sort()) renderTree(child, byCommod, parent, depth + 1);
  }

  function renderComposition(nid, letter, date) {
    const body = document.getElementById('composition-body');
    if (!body) return;
    body.innerHTML = '';
    body.style.fontStyle = '';
    body.style.color = '#222';
    const data = (COMP[nid] || {})[letter] && COMP[nid][letter][date];
    if (!data || Object.keys(data).length === 0) {
      body.style.color = '#999';
      body.style.fontStyle = 'italic';
      body.textContent = '(no per-commodity data for this cell at this date)';
      return;
    }
    const title = document.createElement('div');
    title.style.cssText = 'color:#888;margin-bottom:6px;font-size:11px;';
    title.textContent = nid + ' / ' + letter + ' / ' + date;
    body.appendChild(title);
    for (const root of ROOTS) renderTree(root, data, body, 0);
  }

  // Wrap the existing plotVar so the composition panel updates alongside the chart
  const _orig = window.plotVar;
  if (_orig) {
    window.plotVar = function(nid, v, td) {
      _orig.call(window, nid, v, td);
      const date = document.getElementById('date-picker').value;
      renderComposition(nid, v, date);
    };
  }

  // Also re-render if the date picker changes after a cell is selected
  const datePicker = document.getElementById('date-picker');
  if (datePicker) {
    datePicker.addEventListener('change', () => {
      const selected = document.querySelector('table.balance tr.selected td.has-data[data-nid][data-var]');
      if (selected) renderComposition(selected.dataset.nid, selected.dataset.var, datePicker.value);
    });
  }
})();
</script>
"""


def main():
    g = NetworkGraph(SCENARIO)
    print(f"Using {g}")

    (assets, variables, parent_via_formula, node_values,
     node_roles, agg_sums, formulas, edges_out) = resolver_ui.fetch_from_engine(g)
    print(f"  nodes with values: {len(node_values)}, nodes with edges: {len(edges_out)}")

    payload = base.build_payload(assets, variables, parent_via_formula,
                                  node_values, node_roles, agg_sums, formulas)
    for nid, n in payload["nodes"].items():
        n["edges"] = edges_out.get(nid, {})

    kept_dates = [_date.fromisoformat(d) for d in payload["dates"]]
    sum_kids, intra = resolver_ui.load_partition_sums(SCENARIO, kept_dates)
    for nid, n in payload["nodes"].items():
        n["sum_kids"] = sum_kids.get(nid, {})
        n["intra"] = intra.get(nid, {})

    # Multi-parent rendering (copied verbatim from make_balance_resolver_ui)
    for nid in g.nodes:
        parents = g.partition_parents(nid)
        if len(parents) <= 1:
            continue
        for p in parents:
            sec = payload["nodes"].get(p)
            if sec is None: continue
            kids = sec.setdefault("children", [])
            if nid not in kids: kids.append(nid)
        for p in parents:
            sec = payload["nodes"].get(p)
            if sec: sec["children"].sort()

    print(f"  balance roots: {len(payload['balance_roots'])}, "
          f"constraint roots: {len(payload['constraint_roots'])}, "
          f"dates: {len(payload['dates'])}")

    html = base.HTML_TEMPLATE.replace("__DATA__",
                                       json.dumps(payload, separators=(",", ":"),
                                                  default=float))
    title_marker = ">oil_network &middot; balance equation per node<"
    banner_marker = (">oil_network &middot; balance equation per node &middot; "
                     "<em>hierarchy (scenario: crude_starter_with_grades)</em><")
    html = html.replace(title_marker, banner_marker)

    # Composition panel: per-commodity values for every (node, letter, date)
    composition, hierarchy_edges, commodities = fetch_composition(kept_dates)
    composition["_hierarchy_edges"] = hierarchy_edges
    composition["_commodities"] = commodities
    patch = COMPOSITION_PATCH.replace("__COMPOSITION_JSON__",
                                       json.dumps(composition, separators=(",", ":"), default=float))
    html = html.replace("</body>", patch + "\n</body>")
    n_grade_cells = sum(1 for n in composition.values() if isinstance(n, dict)
                        for l in n.values() for d in l.values() for c in d if c != 'crude')
    print(f"  composition: {len(commodities)} commodities, {n_grade_cells:,} non-crude cells")

    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
                              notes=f"{len(payload['nodes'])} nodes, {len(payload['dates'])} dates")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)  "
          f"[artefact_id={artefact_id}, run_id={run_id}]")


if __name__ == "__main__":
    main()
