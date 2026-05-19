"""Repoint the foreign_supply TS bindings from padd{1,3,5}_view to padd{1,3,5}_imports_agg.

Architectural motivation (Pedro, 2026-05-13):

    padd{N}_imports_agg is the abstract aggregate node that physically receives
    foreign tanker imports and routes them to East/Gulf/West-Coast refineries.
    padd{N}_view is the observational region aggregate (Principle 2.6).

    Before this migration, the EIA-derived series `foreign_supply_to_padd{N}_kbd`
    was bound as authoritative on padd{N}_view, and padd{N}_imports_agg.inflow
    was a backward alias of it. That asserted the same boundary flow on two
    edges crossing the partition (one into padd_view, one into imports_agg)
    and inverted the Principle 2.6 layering (the physical-layer aggregate
    should be authoritative; the observational view should derive).

    After this migration: the TS is authoritative on imports_agg; padd_view
    derives via alias. Same numerical values; one authoritative source per
    physical flow.

Affected: PADDs 1, 3, 5 (PADDs 2 and 4 have no imports_agg — inland, no
maritime imports — and are already clean).

Idempotent: re-running is a no-op once the new state is in place.
Reversible: swap timeseries_id and formula between the two variables.
"""

from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

PADDS = (1, 3, 5)


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        for p in PADDS:
            view_var = f"inflow__crude__padd{p}_view__foreign_supply"
            agg_var = f"inflow__crude__padd{p}_imports_agg__foreign_supply"
            ts_id = f"eia:foreign_supply_to_padd{p}_kbd"

            # Schema check constraint: exactly one of (timeseries_id, formula)
            # must be non-null. So when binding a TS, formula is NULL — the
            # node_type_default_formulas layer surfaces a sensible default in
            # v_effective_assignments.

            # Step 1: move the TS from padd_view to imports_agg.
            cur.execute(
                """
                UPDATE oil_network.variable_assignments
                SET timeseries_id = %s,
                    formula = NULL,
                    formula_inputs = ARRAY[]::text[],
                    notes = COALESCE(notes,'') || ' | repointed 2026-05-13: authoritative TS moved here from padd_view'
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (ts_id, SCENARIO, agg_var),
            )
            n_agg = cur.rowcount

            # Step 2: invert the alias on padd_view (derive from imports_agg).
            # Resolver alias rule expects bare variable_id as formula text;
            # the alias() wrapper is only the label the resolver writes to
            # formula_used.
            cur.execute(
                """
                UPDATE oil_network.variable_assignments
                SET timeseries_id = NULL,
                    formula = %s,
                    formula_inputs = ARRAY[%s]::text[],
                    notes = COALESCE(notes,'') || ' | repointed 2026-05-13: TS moved to imports_agg, this now alias-derived'
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (agg_var, agg_var, SCENARIO, view_var),
            )
            n_view = cur.rowcount

            print(f"PADD {p}: {n_agg} imports_agg rows updated, {n_view} padd_view rows updated")

        conn.commit()

    print("\nDone. Re-run resolve_scenario.py to refresh scenario_resolved_values.")


if __name__ == "__main__":
    main()
