"""Audit flow consistency at every node in the oil_network graph.

For each node, compute:
  in_resolved  / in_latent  / in_missing   = #inflow  variables with a value / latent() / no resolver row
  out_resolved / out_latent / out_missing  = same for outflows
  sum_in / sum_out                          = sums of resolved values (kbd)

Flags raised:
  ASYMMETRIC  — one side has resolved values, the other is entirely latent or missing.
                These are Principle-2.11 latent allocation points (gathering systems,
                hubs, junction pipelines). The framework treats them correctly; this
                report just surfaces where data coverage is one-sided.
  TOPOLOGY    — a node has zero declared edges on a side where physically it should
                (e.g. a hub with no outflows declared at all). True framework bug.
  COVERAGE    — variables exist but produce no rows at the audit date (sparse TS).

The second part of the report traces production-chain flows from each basin /
boundary source through gathering → origin → pipeline → hub → refinery, printing
the resolution status at every step.

Output: a structured plain-text report. Idempotent.

Usage:
  python audit_flow_consistency.py                   # default date = 2024-12-01
  python audit_flow_consistency.py 2024-06-01        # override audit date
"""
from __future__ import annotations

import sys
from collections import defaultdict, deque

import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"
DATE = sys.argv[1] if len(sys.argv) > 1 else "2024-12-01"

# Nodes that are physically sources (only outflow expected) or sinks (only inflow
# expected). Treating these as "asymmetric" would be a false positive.
PURE_SOURCES = {
    "permian_tx", "permian_nm", "bakken_nd", "bakken_mt", "eagle_ford_tx",
    "alaska_north_slope", "california_conventional", "oklahoma_conventional",
    "wyoming_conventional", "colorado_conventional", "gulf_of_america",
    "canadian_oil_sands", "foreign_supply",
    # PADD residual + state residual nodes act as basin-like sources
    "padd1_other", "padd2_other", "padd3_other", "padd4_other", "padd5_other",
    "montana_other", "texas_other",
    # Production aggregates (their "edges" are aggregation, handled separately)
    "permian", "bakken", "eagle_ford", "spr_total", "usa_lower48_excl_gom_view",
    "texas_state_view", "montana_state_view",
}
PURE_SINKS = {
    "foreign_export_destination",
    # Refineries consume crude and emit refined products outside this graph's
    # scope — they have no crude outflows by design (Principle 2.1 + the crude-
    # only commodity scope).
}


def _load_refinery_sinks(cur):
    cur.execute(
        "SELECT node_id FROM oil_network.nodes WHERE node_id LIKE 'ref\\_%' ESCAPE '\\'"
    )
    return {r[0] for r in cur.fetchall()}


def fmt(v):
    return f"{v:>8.1f}" if v is not None else "       —"


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        PURE_SINKS.update(_load_refinery_sinks(cur))
        # ---------- 1. Per-node, per-direction tallies ------------------------
        cur.execute(
            """
            WITH all_flow_vars AS (
                SELECT variable_id, node_id, variable_type
                FROM oil_network.variables
                WHERE variable_type IN ('inflow', 'outflow')
            ),
            resolved AS (
                SELECT variable_id, value, source
                FROM oil_network.scenario_resolved_values
                WHERE scenario_id = %s AND observation_date = %s
            )
            SELECT
                v.node_id,
                v.variable_type,
                COUNT(*)                                              AS n_total,
                COUNT(r.variable_id)                                  AS n_with_row,
                COUNT(*) FILTER (WHERE r.source NOT IN ('latent','zero','unresolved') AND r.value IS NOT NULL) AS n_resolved,
                COUNT(*) FILTER (WHERE r.source = 'latent')           AS n_latent,
                COUNT(*) FILTER (WHERE r.source = 'zero')             AS n_zero,
                COUNT(*) FILTER (WHERE r.source = 'unresolved')       AS n_unresolved,
                COALESCE(SUM(r.value) FILTER (
                    WHERE r.source NOT IN ('latent','zero','unresolved')
                ), 0) AS sum_value
            FROM all_flow_vars v
            LEFT JOIN resolved r USING (variable_id)
            GROUP BY v.node_id, v.variable_type
            """,
            (SCENARIO, DATE),
        )

        tally = defaultdict(lambda: defaultdict(dict))  # tally[node][dir] = {...}
        for row in cur.fetchall():
            nid, vt, n_total, n_with_row, n_res, n_lat, n_zero, n_unres, sum_v = row
            tally[nid][vt] = dict(
                n_total=n_total,
                n_with_row=n_with_row,
                n_resolved=n_res,
                n_latent=n_lat,
                n_zero=n_zero,
                n_unresolved=n_unres,
                n_missing=n_total - n_with_row,
                sum_value=float(sum_v or 0.0),
            )

        # ---------- 2. Classify every node ------------------------------------
        rows = []
        for nid in sorted(tally.keys()):
            ti = tally[nid].get("inflow", {})
            to = tally[nid].get("outflow", {})

            n_in  = ti.get("n_total", 0)
            n_out = to.get("n_total", 0)
            in_resolved  = ti.get("n_resolved", 0)
            out_resolved = to.get("n_resolved", 0)
            in_latent    = ti.get("n_latent", 0)
            out_latent   = to.get("n_latent", 0)
            in_missing   = ti.get("n_missing", 0)
            out_missing  = to.get("n_missing", 0)
            sum_in  = ti.get("sum_value", 0.0)
            sum_out = to.get("sum_value", 0.0)

            # Classify
            is_pure_source = nid in PURE_SOURCES
            is_pure_sink   = nid in PURE_SINKS
            has_in_edges   = n_in  > 0
            has_out_edges  = n_out > 0
            in_side_has_value  = in_resolved  > 0
            out_side_has_value = out_resolved > 0

            if not has_in_edges and not has_out_edges:
                flag = "NO-EDGES"   # node has no flow vars at all (rare)
            elif is_pure_source:
                # Should only have outflows
                if has_in_edges:
                    flag = "TOPOLOGY  src has in"
                elif out_side_has_value:
                    flag = "source OK"
                elif out_latent > 0:
                    flag = "source LATENT"
                else:
                    flag = "source COVERAGE"
            elif is_pure_sink:
                if has_out_edges:
                    flag = "TOPOLOGY  sink has out"
                elif in_side_has_value:
                    flag = "sink OK"
                elif in_latent > 0:
                    flag = "sink LATENT"
                else:
                    flag = "sink COVERAGE"
            else:
                # Pass-through
                if not has_in_edges:
                    flag = "TOPOLOGY  no inflows"
                elif not has_out_edges:
                    flag = "TOPOLOGY  no outflows"
                elif in_side_has_value and out_side_has_value:
                    flag = "BOTH resolved"
                elif in_side_has_value and not out_side_has_value:
                    flag = "ASYM in-only"
                elif out_side_has_value and not in_side_has_value:
                    flag = "ASYM out-only"
                else:
                    flag = "all latent/missing"

            rows.append(dict(
                node_id=nid, flag=flag,
                n_in=n_in, in_resolved=in_resolved, in_latent=in_latent, in_missing=in_missing, sum_in=sum_in,
                n_out=n_out, out_resolved=out_resolved, out_latent=out_latent, out_missing=out_missing, sum_out=sum_out,
            ))

        # ---------- 3. Topology bugs (genuinely concerning) -------------------
        print(f"\n=== Audit at {DATE}, scenario {SCENARIO} ===")
        topo_bugs = [r for r in rows if r["flag"].startswith("TOPOLOGY")]
        print(f"\n[1] TOPOLOGY bugs  ({len(topo_bugs)})")
        if not topo_bugs:
            print("    none — every node has the edges its physical role implies.")
        else:
            for r in topo_bugs:
                print(f"    {r['node_id']:<35} {r['flag']}  in={r['n_in']} out={r['n_out']}")

        # ---------- 4. Asymmetric junctions (Principle-2.11 latent splits) ----
        asym = [r for r in rows if r["flag"].startswith("ASYM")]
        print(f"\n[2] ASYMMETRIC junctions ({len(asym)})  — observed one side, latent/missing on the other.")
        print(f"    Principle 2.11: latent allocation at junctions. Framework-correct;")
        print(f"    surfacing the data-coverage one-sidedness for visibility.\n")
        print(f"    {'node_id':<32}  {'side':<8}  {'res/lat/miss in':<16}  {'sum_in':>9}  {'res/lat/miss out':<16}  {'sum_out':>9}")
        for r in asym:
            side = "in-only" if r["flag"].endswith("in-only") else "out-only"
            in_tag  = f"{r['in_resolved']}/{r['in_latent']}/{r['in_missing']}"
            out_tag = f"{r['out_resolved']}/{r['out_latent']}/{r['out_missing']}"
            print(f"    {r['node_id']:<32}  {side:<8}  {in_tag:<16}  {fmt(r['sum_in'])}  {out_tag:<16}  {fmt(r['sum_out'])}")

        # ---------- 5. Both resolved + diff (closure check for pass-throughs) -
        both = [r for r in rows if r["flag"] == "BOTH resolved"]
        print(f"\n[3] PASS-THROUGHS with both sides observed ({len(both)})  — closure check (sum_in vs sum_out).")
        print(f"    Difference = inventory Δ + balancing item + zero-flow latents.\n")
        print(f"    {'node_id':<32}  {'sum_in':>9}  {'sum_out':>9}  {'diff':>9}")
        for r in both:
            diff = r["sum_in"] - r["sum_out"]
            print(f"    {r['node_id']:<32}  {fmt(r['sum_in'])}  {fmt(r['sum_out'])}  {fmt(diff)}")

        # ---------- 6. Production-chain trace ---------------------------------
        # Walk downstream from each basin/source, printing the resolution status
        # at every step. Stop at terminals/exports/refineries (depth-limited).
        print(f"\n[4] PRODUCTION-CHAIN TRACES from each basin / boundary source\n")

        # Build outgoing-edge graph from the resolver values
        cur.execute(
            """
            SELECT v.node_id, v.related_node_id, srv.value, srv.source
            FROM oil_network.variables v
            LEFT JOIN oil_network.scenario_resolved_values srv
              ON srv.variable_id = v.variable_id
             AND srv.scenario_id = %s
             AND srv.observation_date = %s
            WHERE v.variable_type = 'outflow'
            """,
            (SCENARIO, DATE),
        )
        edges_out = defaultdict(list)  # node -> [(target, value, source)]
        for src, tgt, val, src_type in cur.fetchall():
            edges_out[src].append((tgt, val, src_type))

        STARTS = [
            "permian_tx", "permian_nm", "bakken_nd", "bakken_mt", "eagle_ford_tx",
            "alaska_north_slope", "california_conventional",
            "oklahoma_conventional", "wyoming_conventional", "colorado_conventional",
            "gulf_of_america", "canadian_oil_sands",
        ]

        def trace(start, max_depth=4):
            print(f"  {start}")
            visited = set()
            stack = deque([(start, 0, "")])
            while stack:
                node, depth, indent = stack.pop()
                if depth >= max_depth:
                    continue
                for tgt, val, src in sorted(edges_out.get(node, []), key=lambda x: x[0]):
                    if val is not None:
                        val_str = f"{val:>8.1f} kbd  [{src}]"
                    elif src is None:
                        val_str = "        no resolver row"
                    else:
                        val_str = f"               [{src}]"
                    print(f"    {indent}└─→ {tgt:<35} {val_str}")
                    key = (node, tgt)
                    if key not in visited:
                        visited.add(key)
                        stack.append((tgt, depth + 1, indent + "    "))

        for s in STARTS:
            trace(s)
            print()


if __name__ == "__main__":
    main()
