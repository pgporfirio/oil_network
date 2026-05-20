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
from datetime import date as _date

import make_balance_ui as base
import make_balance_resolver_ui as resolver_ui
from network_graph import NetworkGraph
from paths import HTML_DIR
from render_utils import latest_run_id, write_html

SCENARIO = "crude_starter_with_grades"
HTML_OUT = HTML_DIR / "oil_network_balance_hierarchy.html"
VIEW_NAME = "balance_hierarchy"


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

    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
                              notes=f"{len(payload['nodes'])} nodes, {len(payload['dates'])} dates")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)  "
          f"[artefact_id={artefact_id}, run_id={run_id}]")


if __name__ == "__main__":
    main()
