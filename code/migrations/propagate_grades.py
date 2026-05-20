"""Full grade propagator: per-grade variables across the reachable subgraph
from each producer, mirroring the crude variable structure.

For each (basin, grade, share) in BASIN_GRADE_SHARES:
  Producer-level (the source of grade values):
    production__{grade}__{basin} = <share> * production__crude__{basin}
    outflow__{grade}__{basin}__{X} = (outflow__crude__{basin}__{X}
                                       / production__crude__{basin})
                                     * production__{grade}__{basin}
    inflow__{grade}__{X}__{basin}  = latent()  (mirror-promoted)

For each downstream node N reachable from the producers of {grade}:
  Non-relational vars (production, consumption, inventory, balancing_item)
  are created WITHOUT an explicit assignment — node_type_default_formulas
  apply (commodity-agnostic), giving:
    pipeline/gathering/terminal: 0 for everything (correct pass-through)
    storage: S latent, B latent (no per-grade stocks yet)
    refinery: C latent, S latent, B latent (no per-grade slate yet)

For each downstream edge (S, T) reachable from grade producers (excluding
producer-out edges already covered above), the crude formula is substituted
'__crude__' -> '__{grade}__' to give the grade formula. Where crude is
latent, grade is latent. Where crude is derived from an upstream outflow
(the common 'inflow = outflow_upstream_to_me' pattern), grade inherits the
same derivation — so values cascade as far as the underlying crude chain
provides them.

Net result:
  - Producer node: grade values populated (share-derived production +
    proportional outflows)
  - First-hop downstream (gathering / immediate refinery): grade values
    populated via the substituted 'inflow = outflow upstream' formula
  - Beyond first hop at multi-out nodes (hubs): grade values latent
    (because crude itself is latent on those branching outflows; an LP
    or proportional-mixing rule is still needed to break the latency)

Closure verifiable: sum over (g in children(crude)) production_g(basin)
                  = production_crude(basin) at every basin x date.

Affects scenario crude_starter_with_grades only; starter stays single-
commodity. Idempotent.

Replaces the earlier two-step propagator (propagate_grades_at_producers.py
+ _demo_permian_grade_shares.py) — both are now redundant.
"""
from __future__ import annotations
from collections import defaultdict

import psycopg2
from psycopg2.extras import execute_values

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "crude_starter_with_grades"
EFFECTIVE_FROM = "2015-01-01"


BASIN_GRADE_SHARES = {
    "permian_tx":  [("wti_midland", 0.70), ("wtl", 0.20), ("permian_condensate", 0.10)],
    "permian_nm":  [("wti_midland", 0.80), ("wtl", 0.15), ("permian_condensate", 0.05)],
    "bakken_nd":   [("bakken_light", 1.00)],
    "bakken_mt":   [("bakken_light", 1.00)],
    "eagle_ford_tx": [("eagle_ford_light", 0.60), ("eagle_ford_condensate", 0.40)],
    "gulf_of_america": [("mars", 0.40), ("thunder_horse", 0.25), ("poseidon", 0.15),
                        ("southern_green_canyon", 0.10), ("lls", 0.10)],
    "alaska_north_slope": [("ans", 1.00)],
    "california_conventional": [("kern_heavy", 0.60), ("midway_sunset", 0.40)],
    "oklahoma_conventional": [("oklahoma_sweet", 1.00)],
    "colorado_conventional": [("niobrara_sweet", 1.00)],
    "wyoming_conventional":  [("wyoming_sweet", 0.70), ("wyoming_asphaltic", 0.30)],
    "montana_other": [("bakken_light", 1.00)],
    "texas_other":   [("wti_midland", 1.00)],
}

NON_REL_VTYPES = ("production", "consumption", "inventory", "balancing_item")


def main():
    for basin, shares in BASIN_GRADE_SHARES.items():
        assert abs(sum(s for _, s in shares) - 1.0) < 1e-9, f"{basin} shares != 1.0"

    # Group: grade -> list of producers
    grade_producers: dict[str, list[str]] = defaultdict(list)
    for basin, shares in BASIN_GRADE_SHARES.items():
        for g, _ in shares:
            grade_producers[g].append(basin)

    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # Collect ops in lists, bulk-insert at the end
        new_vars: list[tuple] = []          # (vid, vtype, com, node, related, '{}')
        new_assigns: list[tuple] = []       # (scenario, vid, effective_from, ts, formula, inputs, offsets)

        # ----------------- Producer-level (v1.1 logic) -----------------
        for basin, shares in BASIN_GRADE_SHARES.items():
            crude_prod_var = f"production__crude__{basin}"
            cur.execute("SELECT 1 FROM oil_network.variables WHERE variable_id = %s", (crude_prod_var,))
            if not cur.fetchone():
                continue
            # production share
            for g, share in shares:
                vid = f"production__{g}__{basin}"
                new_vars.append((vid, "production", g, basin, None, "{}"))
                new_assigns.append((SCENARIO, vid, EFFECTIVE_FROM, None,
                                    f"{share} * {crude_prod_var}", [crude_prod_var], None))
            # per-edge proportional outflow + paired latent inflow
            cur.execute("""SELECT related_node_id FROM oil_network.variables
                           WHERE node_id=%s AND variable_type='outflow' AND commodity='crude'""", (basin,))
            for (X,) in cur.fetchall():
                out_crude = f"outflow__crude__{basin}__{X}"
                for g, _ in shares:
                    prod_g = f"production__{g}__{basin}"
                    out_g = f"outflow__{g}__{basin}__{X}"
                    in_g  = f"inflow__{g}__{X}__{basin}"
                    new_vars.append((out_g, "outflow", g, basin, X, "{}"))
                    new_vars.append((in_g,  "inflow",  g, X, basin, "{}"))
                    new_assigns.append((SCENARIO, out_g, EFFECTIVE_FROM, None,
                                        f"({out_crude} / {crude_prod_var}) * {prod_g}",
                                        [out_crude, crude_prod_var, prod_g], None))
                    # Inflow on the downstream side references the outflow directly
                    # (same mirror-via-formula pattern as crude). Resolves in topo
                    # order without needing the late mirror-promotion pass.
                    new_assigns.append((SCENARIO, in_g, EFFECTIVE_FROM, None,
                                        out_g, [out_g], None))

        # ----------------- Downstream structural expansion -----------------
        # For each grade, walk v_node_routes from its producers; for every
        # reachable node, instantiate non-rel grade vars (defaults apply);
        # for every reachable edge NOT already covered by producer-out,
        # mirror the crude formula via __crude__ -> __{g}__ substitution.

        # Cache: assignments_by_var[var_id] = (formula, inputs)
        cur.execute("""SELECT variable_id, formula, formula_inputs
                       FROM oil_network.variable_assignments
                       WHERE scenario_id = %s""", (SCENARIO,))
        crude_assigns = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        for g, producers in grade_producers.items():
            # Reachable nodes + edges from this grade's producers
            cur.execute("""SELECT path FROM oil_network.v_node_routes
                           WHERE origin = ANY(%s)""", (producers,))
            reach_nodes = set(producers)
            reach_edges = set()
            for (path,) in cur.fetchall():
                for n in path: reach_nodes.add(n)
                for s, t in zip(path[:-1], path[1:]):
                    reach_edges.add((s, t))

            # Non-relational per-grade variables at every reachable non-producer node
            # (producer node's grade production already added above)
            for n in reach_nodes:
                if n in BASIN_GRADE_SHARES:
                    continue
                for vt in NON_REL_VTYPES:
                    new_vars.append((f"{vt}__{g}__{n}", vt, g, n, None, "{}"))

            # Per-grade flow variables on every reachable edge.
            # Producer-out edges already handled above; skip them here.
            producer_set = set(producers)
            for s, t in reach_edges:
                if s in producer_set:
                    continue
                # Crude inflow and outflow on this edge
                crude_out = f"outflow__crude__{s}__{t}"
                crude_in  = f"inflow__crude__{t}__{s}"
                grade_out = f"outflow__{g}__{s}__{t}"
                grade_in  = f"inflow__{g}__{t}__{s}"
                new_vars.append((grade_out, "outflow", g, s, t, "{}"))
                new_vars.append((grade_in,  "inflow",  g, t, s, "{}"))
                # Substitute crude formulas if they exist
                for c_vid, g_vid in ((crude_out, grade_out), (crude_in, grade_in)):
                    c_form, c_inputs = crude_assigns.get(c_vid, ("latent()", []))
                    if c_form is None or c_form == "" or c_form == "latent()":
                        g_form = "latent()"
                        g_inputs = []
                    else:
                        sub = f"__{g}__"
                        g_form = c_form.replace("__crude__", sub)
                        g_inputs = [i.replace("__crude__", sub) for i in (c_inputs or [])]
                    new_assigns.append((SCENARIO, g_vid, EFFECTIVE_FROM, None,
                                        g_form, g_inputs, None))

        # Deduplicate before bulk insert (a variable may have been queued twice
        # because multiple grades' reachable subgraphs overlapped at the same
        # node/edge — each grade gets its own variable, so dedup by variable_id)
        seen_vars = {}
        for v in new_vars: seen_vars[v[0]] = v
        seen_assigns = {}
        for a in new_assigns: seen_assigns[(a[0], a[1], a[2])] = a

        print(f"[1] bulk-inserting {len(seen_vars)} variables (deduped from {len(new_vars)} ops)")
        execute_values(cur,
            "INSERT INTO oil_network.variables (variable_id, variable_type, commodity, node_id, related_node_id, attributes) "
            "VALUES %s ON CONFLICT DO NOTHING",
            list(seen_vars.values()), page_size=2000)
        n_vars_inserted = cur.rowcount

        print(f"[2] bulk-inserting {len(seen_assigns)} assignments (deduped from {len(new_assigns)} ops)")
        execute_values(cur,
            "INSERT INTO oil_network.variable_assignments "
            "(scenario_id, variable_id, effective_from, timeseries_id, formula, formula_inputs, formula_input_offsets) "
            "VALUES %s ON CONFLICT (scenario_id, variable_id, effective_from) DO UPDATE "
            "SET formula = EXCLUDED.formula, formula_inputs = EXCLUDED.formula_inputs, timeseries_id = NULL",
            list(seen_assigns.values()), page_size=2000)
        n_assigns_written = cur.rowcount

        conn.commit()

        # ----------------- Single-out pass-through sum-conservation -----------------
        # For any pass-through node with exactly 1 outflow edge, conservation gives:
        #   outflow_g(node, only_sink) = sum_of_inflow_g(node)
        # Applies to pipeline/gathering/origin/import/export terminals.
        # NOT storage (inventory dynamics, different rule) or refinery (sink).
        # We instantiate both the variable and the assignment here so it works
        # even for pass-through nodes at the v_node_routes depth boundary (where
        # the (node, only_sink) edge wasn't in any path's edge list and so wasn't
        # picked up by the v2 substitution pass above).

        PASS_THROUGH = ('pipeline', 'gathering', 'origin_terminal',
                        'import_terminal', 'export_terminal',
                        'foreign_production_aggregate', 'foreign_export_destination')

        cur.execute("""
            SELECT v.node_id, MIN(v.related_node_id) AS only_target
            FROM oil_network.variables v
            JOIN oil_network.assets a ON a.asset_id = v.node_id
            WHERE v.variable_type='outflow' AND v.commodity='crude'
              AND a.node_subtype = ANY(%s)
            GROUP BY v.node_id, a.node_subtype
            HAVING COUNT(DISTINCT v.related_node_id) = 1
        """, (list(PASS_THROUGH),))
        single_out_nodes = {nid: target for nid, target in cur.fetchall()}

        # Per-(grade, node) inflow var list from each grade's reachable edges
        from collections import defaultdict as dd
        inflow_vars_by_g_node: dict[tuple[str, str], list[str]] = dd(list)
        for g, producers in grade_producers.items():
            cur.execute("SELECT path FROM oil_network.v_node_routes WHERE origin = ANY(%s)",
                        (producers,))
            for (path,) in cur.fetchall():
                for s, t in zip(path[:-1], path[1:]):
                    inflow_vars_by_g_node[(g, t)].append(f"inflow__{g}__{t}__{s}")

        sum_vars = []
        sum_assigns = []
        for nid, target in single_out_nodes.items():
            if nid in BASIN_GRADE_SHARES:
                continue
            for g in grade_producers:
                in_vars = sorted(set(inflow_vars_by_g_node.get((g, nid), [])))
                if not in_vars:
                    continue
                out_g = f"outflow__{g}__{nid}__{target}"
                # Pair the corresponding inflow on the target too (mirror promotion needs it)
                in_g  = f"inflow__{g}__{target}__{nid}"
                sum_vars.append((out_g, "outflow", g, nid, target, "{}"))
                sum_vars.append((in_g,  "inflow",  g, target, nid, "{}"))
                sum_assigns.append((SCENARIO, out_g, EFFECTIVE_FROM, None,
                                    "sum", in_vars, None))
                # Paired inflow on the downstream side references the outflow
                # (same mirror-via-formula pattern; topo-resolvable).
                sum_assigns.append((SCENARIO, in_g, EFFECTIVE_FROM, None,
                                    out_g, [out_g], None))

        # Dedup
        seen_sv = {v[0]: v for v in sum_vars}
        seen_sa = {(a[0], a[1], a[2]): a for a in sum_assigns}

        print(f"[3.5a] sum-conservation: bulk-insert {len(seen_sv)} variables")
        execute_values(cur,
            "INSERT INTO oil_network.variables (variable_id, variable_type, commodity, node_id, related_node_id, attributes) "
            "VALUES %s ON CONFLICT DO NOTHING",
            list(seen_sv.values()), page_size=2000)

        print(f"[3.5b] sum-conservation: bulk-insert {len(seen_sa)} assignments")
        execute_values(cur,
            "INSERT INTO oil_network.variable_assignments "
            "(scenario_id, variable_id, effective_from, timeseries_id, formula, formula_inputs, formula_input_offsets) "
            "VALUES %s ON CONFLICT (scenario_id, variable_id, effective_from) DO UPDATE "
            "SET formula = EXCLUDED.formula, formula_inputs = EXCLUDED.formula_inputs, timeseries_id = NULL",
            list(seen_sa.values()), page_size=2000)

        print(f"\n[3] result: {n_vars_inserted} new variables in DB, {n_assigns_written} assignment rows touched")
        cur.execute("SELECT COUNT(*) FROM oil_network.variables WHERE commodity != 'crude'")
        print(f"     total non-crude variables: {cur.fetchone()[0]:,}")
        cur.execute("""SELECT v.commodity, COUNT(*) FROM oil_network.variables v
                       WHERE v.commodity != 'crude' GROUP BY v.commodity ORDER BY v.commodity""")
        print(f"     by grade:")
        for c, n in cur.fetchall():
            print(f"       {c:30s}  {n:>6}")


if __name__ == "__main__":
    main()
