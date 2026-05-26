"""Reproduce the balance UI's `treeSumKids` (single-level partition sum with
intra-flow netting) and verify, for every TS-observed aggregate parent at
2024-12-01, that the boundary-classified sum-of-direct-children matches the
parent's observed value when every required descendant has data.

The JS treeSumKids logic (from make_balance_ui.py):
  - Walks ONLY the direct partition children of the parent.
  - For inflow / outflow: iterates each direct child's edges and classifies
    each edge as boundary (other end NOT in parent's descendants) or intra
    (other end IS in parent's descendants).
  - For P / C / I (inventory) / B: sums the direct child's value.
"""
from __future__ import annotations
import psycopg2
from collections import defaultdict

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCEN = "starter_us_crude_2015_2025"
DATE = "2024-12-01"

REL_TYPES = ("inflow", "outflow")

with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
    # Full descendants closure — used for boundary/intra classification.
    cur.execute("""
        WITH RECURSIVE d AS (
            SELECT parent_node_id AS root, child_node_id AS descendant
              FROM oil_network.v_partition_tree WHERE scenario_id=%s
            UNION
            SELECT d.root, pt.child_node_id
              FROM d JOIN oil_network.v_partition_tree pt
                ON pt.parent_node_id = d.descendant AND pt.scenario_id=%s
        )
        SELECT root, ARRAY_AGG(DISTINCT descendant) FROM d GROUP BY root
    """, (SCEN, SCEN))
    descendants = {root: set(kids) for root, kids in cur.fetchall()}

    # Immediate (direct) partition children per (parent, variable_type) — the JS
    # walks `children_partition` which is variable-type-specific.
    cur.execute("""
        SELECT parent_node_id, variable_type, ARRAY_AGG(DISTINCT child_node_id)
          FROM oil_network.v_partition_tree WHERE scenario_id=%s
         GROUP BY parent_node_id, variable_type
    """, (SCEN,))
    direct_children = {(p, vt): set(ch) for p, vt, ch in cur.fetchall()}

    # Every resolved value at DATE.
    cur.execute("""
        SELECT v.node_id, v.variable_type, v.related_node_id, srv.value, srv.source
          FROM oil_network.scenario_resolved_values srv
          JOIN oil_network.variables v ON v.variable_id = srv.variable_id
         WHERE srv.scenario_id=%s AND srv.observation_date=%s AND v.commodity='crude'
    """, (SCEN, DATE))
    by_node = defaultdict(list)  # (node, vtype) -> list of (related_node, value, source)
    for nid, vt, rel, val, src in cur.fetchall():
        by_node[(nid, vt)].append((rel, val, src))

    # TS-observed aggregate parents (parent has TS-bound variable of this vtype).
    cur.execute("""
        SELECT DISTINCT v.node_id, v.variable_type
          FROM oil_network.v_partition_tree pt
          JOIN oil_network.variables v
            ON v.node_id = pt.parent_node_id AND v.variable_type = pt.variable_type AND v.commodity = pt.commodity
          JOIN oil_network.v_effective_assignments ea
            ON ea.variable_id = v.variable_id AND ea.scenario_id = pt.scenario_id
         WHERE pt.scenario_id=%s AND ea.timeseries_id IS NOT NULL
    """, (SCEN,))
    candidates = sorted(cur.fetchall())

    print("=" * 96)
    print(f"BALANCE-UI BOUNDARY-SUM CHECK @ {DATE}  (direct partition children only, JS treeSumKids logic)")
    print("=" * 96)
    print(f"  {'parent':<28}{'vtype':<14}{'parent':>14}{'bdy_sum':>11}{'intra':>10}{'gap':>9}{'kids':>6}  verdict")

    n_match = n_mismatch = n_incomplete = 0
    for parent, vtype in candidates:
        children = direct_children.get((parent, vtype), set())
        if not children:
            continue
        all_desc = descendants.get(parent, set())

        # Parent total — lump related_nodes for relational types.
        parent_entries = by_node.get((parent, vtype), [])
        parent_total = 0.0
        parent_has_val = False
        for r, v, src in parent_entries:
            if v is not None and src not in ("latent", "unresolved"):
                parent_total += v
                parent_has_val = True
        if not parent_has_val:
            continue

        boundary_sum = 0.0
        intra_sum = 0.0
        n_missing = 0
        n_classified = 0
        for kid in children:
            entries = by_node.get((kid, vtype), [])
            if vtype in REL_TYPES:
                # Use this kid's edges. classify each by related_node membership.
                for r, v, src in entries:
                    if v is None or src in ("latent", "unresolved"):
                        n_missing += 1
                        continue
                    if r and r in all_desc:
                        intra_sum += v
                    else:
                        boundary_sum += v
                    n_classified += 1
            else:
                # Non-relational: sum kid's value directly. Typically one entry.
                got = False
                for r, v, src in entries:
                    if v is None or src in ("latent", "unresolved"):
                        continue
                    boundary_sum += v
                    got = True
                if not got:
                    n_missing += 1

        gap = parent_total - boundary_sum
        tol = max(1.0, abs(parent_total) * 0.01)
        incomplete = n_missing > 0
        if incomplete:
            verdict = f"skipped ({n_missing} missing)"; n_incomplete += 1
        elif abs(gap) <= tol:
            verdict = "MATCH"; n_match += 1
        else:
            verdict = "MISMATCH"; n_mismatch += 1

        if not incomplete or vtype != "inflow":  # show everything except trivially skipped
            if not incomplete:
                print(f"  {parent:<28}{vtype:<14}{parent_total:>14,.0f}{boundary_sum:>11,.0f}{intra_sum:>10,.0f}{gap:>9,.1f}{len(children):>6}  {verdict}")

    print()
    print(f"  Summary: {n_match} MATCH, {n_mismatch} MISMATCH, {n_incomplete} skipped (some children missing/latent)")
