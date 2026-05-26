"""Audit partition-closure gaps in the balance UI.

For each PADD and for usa_view, compute:
  own.I      = sum of inflow edges on the node itself (aggregate-level)
  own.O      = sum of outflow edges on the node itself
  bdy.I      = sum of children's boundary inflows  (cross-partition)
  bdy.O      = same for outflows
  intra.I/O  = sum of children's intra-partition flows (informational)
  gap.I/O    = own − bdy   (where the sum-of-children fails to close)

Then categorise the gap by walking each of the node's own edges and asking:
  - is there a pipe-level decomposition declared?
  - are those pipes resolved (TS) or latent?
  - if no pipe declared, the gap is "no pipe modeled for this corridor"

Output: a small report.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date as _date

import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"
DATE = "2024-12-01"


def fmt(v):
    return f"{float(v):>9.1f}" if v is not None else "      —"


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # 1. Aggregate per-node values from the resolver
        cur.execute(
            """
            SELECT v.node_id, v.variable_type, SUM(srv.value) AS val
            FROM oil_network.scenario_resolved_values srv
            JOIN oil_network.variables v USING (variable_id)
            WHERE srv.scenario_id = %s
              AND srv.observation_date = %s
              AND srv.value IS NOT NULL
              AND srv.source <> 'zero'
              AND v.node_id IN ('usa_view','padd1_view','padd2_view','padd3_view','padd4_view','padd5_view')
              AND v.variable_type IN ('inflow','outflow')
            GROUP BY v.node_id, v.variable_type
            """,
            (SCENARIO, DATE),
        )
        own = defaultdict(dict)
        for nid, vt, val in cur.fetchall():
            own[nid][vt] = float(val)

        # 2. Per-edge inflow/outflow values for every node (we'll filter by parent
        #    descendants in Python).
        cur.execute(
            """
            SELECT v.node_id, v.related_node_id, v.variable_type, srv.value
            FROM oil_network.scenario_resolved_values srv
            JOIN oil_network.variables v USING (variable_id)
            WHERE srv.scenario_id = %s
              AND srv.observation_date = %s
              AND v.variable_type IN ('inflow','outflow')
              AND v.related_node_id IS NOT NULL
              AND srv.value IS NOT NULL
              AND srv.source <> 'zero'
            """,
            (SCENARIO, DATE),
        )
        edges_by_node: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for nid, rel, vt, val in cur.fetchall():
            edges_by_node[nid].append((rel, vt, float(val)))

        # 3. Tree structure — read from v_partition_tree, the database view
        #    that encodes the same-type aggregation hierarchy (twelfth pass).
        #    Previously this section duplicated the SQL inline.
        cur.execute(
            """
            SELECT parent_node_id, child_node_id
            FROM oil_network.v_partition_tree
            WHERE scenario_id = %s
            GROUP BY parent_node_id, child_node_id
            """,
            (SCENARIO,),
        )
        children: dict[str, set[str]] = defaultdict(set)
        for parent, child in cur.fetchall():
            children[parent].add(child)

        def descendants(root: str) -> set[str]:
            out = {root}; stack = [root]
            while stack:
                cur_n = stack.pop()
                for c in children.get(cur_n, ()):
                    if c not in out:
                        out.add(c); stack.append(c)
            return out

        # 4. For each node we audit, compute boundary + intra contributions
        #    and break down the gap.
        cur.execute(
            """
            SELECT v.node_id, v.related_node_id, v.variable_type, srv.value, va.formula_inputs
            FROM oil_network.variable_assignments va
            JOIN oil_network.variables v ON v.variable_id = va.variable_id
            LEFT JOIN oil_network.scenario_resolved_values srv ON srv.scenario_id = %s
                AND srv.observation_date = %s
                AND srv.variable_id = va.variable_id
            WHERE va.scenario_id = %s
              AND v.variable_type IN ('inflow','outflow')
              AND v.related_node_id IS NOT NULL
              AND v.node_id IN ('usa_view','padd1_view','padd2_view','padd3_view','padd4_view','padd5_view')
            """,
            (SCENARIO, DATE, SCENARIO),
        )
        # Map: (node_id, vtype) -> list of (related_node, value, formula_inputs)
        own_edges = defaultdict(list)
        for nid, rel, vt, val, fi in cur.fetchall():
            own_edges[(nid, vt)].append((rel, float(val) if val is not None else None, list(fi or [])))

        # 5. Walk and report
        print(f"=== Partition-closure audit at {DATE} ===\n")
        for nid in ("usa_view", "padd1_view", "padd2_view", "padd3_view",
                    "padd4_view", "padd5_view"):
            desc = descendants(nid)
            own_i = own.get(nid, {}).get("inflow")
            own_o = own.get(nid, {}).get("outflow")
            # sum-of-children boundary/intra
            kids = list(children.get(nid, ()))
            bdy_i = intra_i = 0.0
            bdy_o = intra_o = 0.0
            for c in kids:
                for (rel, vt, val) in edges_by_node.get(c, []):
                    if vt == "inflow":
                        if rel in desc: intra_i += val
                        else:           bdy_i   += val
                    elif vt == "outflow":
                        if rel in desc: intra_o += val
                        else:           bdy_o   += val

            print(f"{nid}")
            print(f"  own.I        = {fmt(own_i)}        own.O = {fmt(own_o)}")
            print(f"  bdy.I (kids) = {fmt(bdy_i)}        bdy.O = {fmt(bdy_o)}")
            print(f"  intra (kids) = {fmt(intra_i)}        intra = {fmt(intra_o)}")
            gap_i = (own_i or 0) - bdy_i
            gap_o = (own_o or 0) - bdy_o
            print(f"  gap.I        = {fmt(gap_i)}        gap.O = {fmt(gap_o)}")

            # Break down the gap: walk each of the node's own edges, decide
            # whether the per-pipe decomposition is declared and whether the
            # pipes are resolved.
            if abs(gap_i) > 1 or abs(gap_o) > 1:
                print("  -- breakdown by own edges --")
                for vt in ("inflow", "outflow"):
                    for (rel, val, fi) in own_edges.get((nid, vt), []):
                        if val is None or val == 0:
                            continue
                        if fi:
                            # Get the sum of resolved pipe values for this corridor
                            cur.execute(
                                """
                                SELECT COALESCE(SUM(value), 0) AS s,
                                       COUNT(*) FILTER (WHERE value IS NULL) AS n_null,
                                       COUNT(*)                              AS n_total
                                FROM oil_network.scenario_resolved_values
                                WHERE scenario_id = %s AND observation_date = %s
                                  AND variable_id = ANY(%s)
                                """,
                                (SCENARIO, DATE, fi),
                            )
                            s, n_null, n_total = cur.fetchone()
                            status = (
                                "all latent" if n_null == n_total
                                else f"{n_total - n_null}/{n_total} resolved, sum={float(s):.1f}"
                            )
                            print(f"    {vt:7s} {rel:30s} {val:>7.1f}  pipes: [{', '.join(fi)}]")
                            print(f"            -> pipe sum status: {status}")
                        else:
                            print(f"    {vt:7s} {rel:30s} {val:>7.1f}  no pipe decomposition declared")
            print()


if __name__ == "__main__":
    main()
