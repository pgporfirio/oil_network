"""Clean up the two Canadian-corridor TS-binding collisions.

Background (Pedro, 2026-05-13):

    The audit `audit_ts_binding_uniqueness.py` flagged two cases where the same
    EIA series is bound to two different variables in the starter scenario:

      eia:MCRIPP4CA2  ->  inflow__crude__padd4_view__canadian_oil_sands
                          outflow__crude__canadian_oil_sands__pipe_express_platte

      eia:MCRIPP5CA2  ->  inflow__crude__padd5_view__canadian_oil_sands
                          outflow__crude__canadian_oil_sands__pipe_trans_mountain_tmx

    Each TS is the PADD-level "imports from Canada" series. Because PADDs 4 and 5
    each have exactly one Canadian pipe corridor (Express+Platte and TMX
    respectively), the PADD-aggregate series equals the single-corridor pipe
    flow. The framework had asserted the same observation on two outflow edges
    from `canadian_oil_sands` — making its summed outflow appear double the
    true physical flow if both edges were treated as independent.

    Fix (same pattern as repoint_foreign_supply_to_imports_agg.py):
      - Keep TS on canadian_oil_sands -> pipe_* (physical entry point)
      - Convert padd_view.inflow <- canadian_oil_sands to alias-derived

After the migration the resolver should produce identical values and the
TS-uniqueness audit should report zero collisions.

Idempotent: re-running once the new state is in place is a no-op.
"""

from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

CORRIDORS = (
    (4, "pipe_express_platte"),
    (5, "pipe_trans_mountain_tmx"),
)


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        for padd, pipe in CORRIDORS:
            view_var = f"inflow__crude__padd{padd}_view__canadian_oil_sands"
            pipe_outflow_var = f"outflow__crude__canadian_oil_sands__{pipe}"

            # Resolver alias rule (resolve_scenario.py L288) matches when
            # `formula in by_id` — i.e. the formula text is the bare
            # variable_id, not wrapped in alias(). The wrapped form is only the
            # display label that the resolver writes back to formula_used.
            cur.execute(
                """
                UPDATE oil_network.variable_assignments
                SET timeseries_id = NULL,
                    formula = %s,
                    formula_inputs = ARRAY[%s]::text[],
                    notes = COALESCE(notes,'') || ' | repointed 2026-05-13: TS kept on Canada-side outflow, this now alias-derived'
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (pipe_outflow_var, pipe_outflow_var, SCENARIO, view_var),
            )
            print(f"PADD {padd} ({pipe}): {cur.rowcount} row(s) updated")

        conn.commit()

    print("\nDone. Re-run resolve_scenario.py and audit_ts_binding_uniqueness.py to confirm.")


if __name__ == "__main__":
    main()
