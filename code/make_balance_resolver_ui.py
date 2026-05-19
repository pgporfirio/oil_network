"""Generate oil_network_balance_resolver.html — the balance UI built on top of
the NetworkGraph engine.

This is the canonical pipeline:
    1. resolve_scenario.py        (writes scenario_resolved_values,
                                   refreshes layer-4 materialised views)
    2. NetworkGraph(scenario_id)  (loads from L3/L4 views — single Python
                                   source of truth for renderers)
    3. this script                (formats the graph object as HTML)

No SQL queries here. Every datum comes from the NetworkGraph API:
  - `g.nodes` for asset metadata
  - `g.variables()` for the variable list
  - `g.partition_children`/`partition_parents` for the partition tree
    (which itself reads from `v_partition_tree`)
  - `g.role(node_id)` for scenario node_role
  - `g.pcisob(node_id, date)` for the per-letter balance values
  - `g.per_edge_values(node_id, vt, date)` for the I/O edge breakdown

Output: clean/outputs/html/oil_network_balance_resolver.html.
"""
from __future__ import annotations
from paths import HTML_DIR

import json
from collections import defaultdict
from datetime import date as _date, timedelta
from pathlib import Path

import psycopg2
import make_balance_ui as base  # HTML_TEMPLATE + build_payload (template provider)
from network_graph import NetworkGraph
from render_utils import latest_run_id, write_html

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

# Map balance-UI letter codes to variable_type strings for v_partition_sums lookup.
LETTER_TO_VTYPE = {
    "P": "production",
    "C": "consumption",
    "S": "inventory",
    "I": "inflow",
    "O": "outflow",
    "B": "balancing_item",
}

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_balance_resolver.html"
SCENARIO = "starter_us_crude_2015_2025"
VIEW_NAME = "balance"

# Date cut-off — exclude anything more recent than today − 90 days. This
# hides the forecast-horizon LOCF artefact (Corollary D-bis): dates past
# the publication horizon of state-level EIA series show LOCF-derived
# residuals diverging from STEO-derived basin forecasts. The cut-off
# leaves only historical dates where all source TS have published values.
DATE_CUTOFF_DAYS = 90


# ---------------------------------------------------------------------------
# Build the payload shape that base.build_payload expects, sourced entirely
# from the NetworkGraph engine.
# ---------------------------------------------------------------------------

# Match make_balance_ui's expected tuple shape:
#   (assets, variables, parent_via_formula, node_values, node_roles,
#    agg_sums, formulas, edges_out)

def fetch_from_engine(g: NetworkGraph):
    # Date cut-off: keep only dates ≤ today − DATE_CUTOFF_DAYS.
    cutoff = _date.today() - timedelta(days=DATE_CUTOFF_DAYS)
    dates_kept = [d for d in g.dates if d <= cutoff]
    print(f"  date filter: {len(dates_kept)}/{len(g.dates)} dates kept "
          f"(<= {cutoff}; cutoff = today − {DATE_CUTOFF_DAYS}d)")

    # 1. Assets — list of dicts matching the original shape
    assets = [
        {"asset_id": nid, "name": n.name, "kind": n.kind,
         "node_class": n.node_class, "node_subtype": n.node_subtype}
        for nid, n in g.nodes.items()
    ]
    assets.sort(key=lambda a: a["asset_id"])

    # 2. Variables
    variables = [
        {"variable_id": v["variable_id"], "node_id": v["node_id"],
         "variable_type": v["variable_type"]}
        for v in g.variables()
    ]

    # 3. Parent map
    parent_via_formula: dict[str, str] = {}
    for nid in g.nodes:
        for parent in g.partition_parents(nid):
            if nid not in parent_via_formula:
                parent_via_formula[nid] = parent
                break

    # 4. Per-node values per kept date.
    node_values: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: {k: {} for k in ("P", "C", "I", "O", "B", "S", "dS")})
    for d in dates_kept:
        d_str = d.isoformat()
        for nid in g.nodes:
            pc = g.pcisob(nid, d)
            if not pc:
                continue
            nv = node_values[nid]
            for letter in ("P", "C", "I", "O", "B", "S", "dS"):
                v = pc.get(letter)
                if v is not None:
                    nv[letter][d_str] = float(v)

    # 5. Per-scenario node roles
    node_roles = {nid: g.role(nid) for nid in g.nodes if g.role(nid)}

    # 6. Per-edge resolved I/O values per node per kept date.
    edges_out: dict[str, dict[str, dict[str, list]]] = {}
    for nid in g.nodes:
        for letter, vt in (("I", "inflow"), ("O", "outflow")):
            for d in dates_kept:
                edges = g.per_edge_values(nid, vt, d)
                if not edges:
                    continue
                edges_out.setdefault(nid, {"I": {}, "O": {}})[letter][d.isoformat()] = edges

    return assets, variables, parent_via_formula, dict(node_values), node_roles, {}, {}, edges_out


def load_partition_sums(scenario_id: str, dates_kept: list[_date]):
    """Read v_partition_sums + v_partition_intra_flows for the kept dates.

    Returns two nested dicts:
      sum_kids[node_id][letter][date_str] = float   (the lumped typed-partition sum)
      intra[node_id][letter][date_str]   = float    (informational intra-flow)
    """
    sum_kids: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict))
    intra: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict))
    vt_to_letter = {v: k for k, v in LETTER_TO_VTYPE.items()}
    kept_set = {d.isoformat() for d in dates_kept}

    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT parent_node_id, variable_type, observation_date, sum_children
              FROM oil_network.v_partition_sums
             WHERE scenario_id = %s AND related_node_id IS NULL
        """, (scenario_id,))
        for nid, vt, d, val in cur.fetchall():
            d_str = d.isoformat()
            if d_str not in kept_set:
                continue
            letter = vt_to_letter.get(vt)
            if letter is None or val is None:
                continue
            sum_kids[nid][letter][d_str] = float(val)
        # For non-relational types, also fold in the per-related row (since
        # those don't have a lumped row in the view).
        cur.execute("""
            SELECT parent_node_id, variable_type, observation_date, sum_children
              FROM oil_network.v_partition_sums
             WHERE scenario_id = %s AND related_node_id IS NULL
               AND variable_type IN ('production','consumption','inventory','balancing_item')
        """, (scenario_id,))
        # (Already covered by the first SELECT — left here for clarity in case
        #  the view structure changes.)

        cur.execute("""
            SELECT parent_node_id, variable_type, observation_date, intra_sum
              FROM oil_network.v_partition_intra_flows
             WHERE scenario_id = %s
        """, (scenario_id,))
        for nid, vt, d, val in cur.fetchall():
            d_str = d.isoformat()
            if d_str not in kept_set or val is None:
                continue
            letter = vt_to_letter.get(vt)
            if letter is None:
                continue
            intra[nid][letter][d_str] = float(val)

    return {k: {l: dict(dd) for l, dd in v.items()} for k, v in sum_kids.items()}, \
           {k: {l: dict(dd) for l, dd in v.items()} for k, v in intra.items()}


def main(g: NetworkGraph = None):
    if g is None:
        g = NetworkGraph(SCENARIO)
    print(f"Using {g}")

    (assets, variables, parent_via_formula, node_values,
     node_roles, agg_sums, formulas, edges_out) = fetch_from_engine(g)
    print(f"  nodes with values: {len(node_values)},  nodes with edges: {len(edges_out)}")

    payload = base.build_payload(assets, variables, parent_via_formula,
                                  node_values, node_roles, agg_sums, formulas)

    # Attach per-edge I/O to each node (legacy, used for the detail tooltip).
    for nid, n in payload["nodes"].items():
        n["edges"] = edges_out.get(nid, {})

    # Pre-computed partition sums + intra (from v_partition_sums /
    # v_partition_intra_flows). The JS reads these instead of walking
    # descendants at render time.
    kept_dates = [_date.fromisoformat(d) for d in payload["dates"]]
    sum_kids, intra = load_partition_sums(SCENARIO, kept_dates)
    for nid, n in payload["nodes"].items():
        n["sum_kids"] = sum_kids.get(nid, {})
        n["intra"] = intra.get(nid, {})
    print(f"  partition sums attached: {sum(1 for n in payload['nodes'].values() if n['sum_kids'])} "
          f"nodes have sum_kids data")

    # Multi-parent rendering. Any node with more than one partition parent
    # (notably the inter_padd_*_agg family — receiver+sender both own them
    # via different variable_types) is appended to the children list of every
    # parent that doesn't already include it. The JS's existing intra/bdy
    # filter renders only the relevant edge per parent context — under the
    # sender PADD the agg's outflow_to_receiver shows as bdy.O; under the
    # receiver PADD the agg's inflow_from_sender shows as bdy.I. No
    # double-counting because each edge is on the boundary of only one parent.
    n_multi = 0
    for nid in g.nodes:
        parents = g.partition_parents(nid)
        if len(parents) <= 1:
            continue
        n_multi += 1
        for p in parents:
            sec = payload["nodes"].get(p)
            if sec is None:
                continue
            kids = sec.setdefault("children", [])
            if nid not in kids:
                kids.append(nid)
        # Keep children lists sorted alphabetically for stable rendering
        for p in parents:
            sec = payload["nodes"].get(p)
            if sec:
                sec["children"].sort()
    if n_multi:
        print(f"  multi-parent nodes promoted to both parents: {n_multi}")

    print(f"  balance roots: {len(payload['balance_roots'])}, "
          f"constraint roots: {len(payload['constraint_roots'])}, "
          f"dates: {len(payload['dates'])}")

    html = base.HTML_TEMPLATE.replace("__DATA__",
                                       json.dumps(payload, separators=(",", ":"),
                                                  default=float))
    # Add a small banner above the title to make it obvious which version is open
    title_marker = ">oil_network &middot; balance equation per node<"
    banner_marker = ">oil_network &middot; balance equation per node &middot; <em>resolver</em><"
    html = html.replace(title_marker, banner_marker)

    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
                              notes=f"{len(payload['nodes'])} nodes, "
                                    f"{len(payload['dates'])} dates")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)  "
          f"[artefact_id={artefact_id}, run_id={run_id}]")


if __name__ == "__main__":
    main()
