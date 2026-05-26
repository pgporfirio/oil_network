"""Seventeenth pass: declare pipe_bakken_xstate as a partition member of
both padd2_view and padd4_view via the same inventory-membership pattern
used in the eleventh pass for the gathering nodes.

Rationale (Pedro): the hierarchy is defined by the variables. pipe_bakken_xstate
carries inflow/outflow variables linking it to bakken_nd_gathering (a member
of padd2_view) and bakken_mt_gathering (a member of padd4_view). It is
structurally a multi-PADD node — it should appear under both, not float as
an orphan at the top of the balance HTML.

Implementation: add `inventory__crude__pipe_bakken_xstate` to
`inventory__crude__padd{2,4}_view.formula_inputs`. Numerical effect: zero
(pipe inventory = 0 by pipeline node-type default). Audit effect:
v_partition_tree (refreshed) gains two rows (padd2_view → pipe_bakken_xstate
and padd4_view → pipe_bakken_xstate via inventory type), so the renderer's
multi-parent rendering displays it under both PADDs.

Idempotent: skips the insertion if the input is already present.
"""
from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

PADDS = ("padd2_view", "padd4_view")
XSTATE_INV = "inventory__crude__pipe_bakken_xstate"


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        for padd in PADDS:
            inv_var = f"inventory__crude__{padd}"
            cur.execute(
                """
                SELECT timeseries_id, formula, formula_inputs, notes
                FROM oil_network.variable_assignments
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (SCENARIO, inv_var),
            )
            row = cur.fetchone()
            if row is None:
                print(f"  ! no override row for {inv_var} — skipping")
                continue
            ts, formula, existing_inputs, notes = row
            existing = list(existing_inputs or [])
            if XSTATE_INV in existing:
                print(f"  · {inv_var}: pipe_bakken_xstate already a member; skipping")
                continue
            merged = existing + [XSTATE_INV]
            new_notes = (notes or "") + (
                " | seventeenth-pass: pipe_bakken_xstate added "
                "(cross-state Bakken connector; zero S, multi-PADD member)"
            )
            cur.execute(
                """
                UPDATE oil_network.variable_assignments
                SET formula_inputs = %s, notes = %s
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (merged, new_notes, SCENARIO, inv_var),
            )
            print(f"  ✓ {inv_var}: +inventory__crude__pipe_bakken_xstate")
        conn.commit()
    print()
    print("Next: refresh_views.py --structural to update v_partition_tree.")


if __name__ == "__main__":
    main()
