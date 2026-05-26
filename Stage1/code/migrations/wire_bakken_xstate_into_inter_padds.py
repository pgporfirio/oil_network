"""Wire pipe_bakken_xstate as a partition constituent of the inter_padd 2↔4
aggregates' outflow variables.

Rationale (PROJECT_STATE §8 item 1; HANDOVER 11th-pass follow-up):
The bakken cross-state connector models real-world midstream pooling between
ND and MT — Hess, Crestwood, Energy Transfer all operate gathering trunks
across the state line. Its 4 flow variables are latent in the starter.

EIA publishes `MCRMP*` series capturing the total crude movement between PADDs.
Whatever the Bakken cross-state pooling moves between PADD-2 and PADD-4 is
part of those EIA totals — it's the same physical crude. The wiring formalises
that the inter_padd_{2_to_4,4_to_2}_agg.outflow's TS-observed value decomposes
into [pipe_bakken_xstate flow] + [pipes/tankers explicitly modelled] +
[latent residual].

Mapping by direction:
- inter_padd_2_to_4_agg.outflow → padd4_view receives the EIA P2→P4 total.
  The xstate outflow that lands in PADD4 is the one targeting bakken_mt_gathering.
- inter_padd_4_to_2_agg.outflow → padd2_view receives the EIA P4→P2 total.
  The xstate outflow that lands in PADD2 is the one targeting bakken_nd_gathering.

Numerical effect: ZERO. Both xstate outflows are `latent()`, so they resolve
to NULL and the parent's TS value passes through unchanged. The
v_aggregation_consistency status stays `ok` (with one more declared input,
one more latent input — net no change to gap_kbd).

Audit effect: v_partition_tree picks up two new inter_padd_agg → pipe_bakken_xstate
edges, so future per-operator Bakken gathering telemetry lands cleanly on the
right partition slot rather than appearing as an orphan flow.

Idempotent: skips insertion if the input is already present.
"""
from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

# (parent variable_id to update, formula_input variable_id to add)
WIRINGS = [
    (
        "outflow__crude__inter_padd_2_to_4_agg__padd4_view",
        "outflow__crude__pipe_bakken_xstate__bakken_mt_gathering",
    ),
    (
        "outflow__crude__inter_padd_4_to_2_agg__padd2_view",
        "outflow__crude__pipe_bakken_xstate__bakken_nd_gathering",
    ),
]


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        for parent_var, xstate_var in WIRINGS:
            cur.execute(
                """
                SELECT formula_inputs, notes
                FROM oil_network.variable_assignments
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (SCENARIO, parent_var),
            )
            row = cur.fetchone()
            if row is None:
                print(f"  ! no override row for {parent_var} — skipping")
                continue
            existing_inputs, notes = row
            existing = list(existing_inputs or [])
            if xstate_var in existing:
                print(f"  · {parent_var}: already has {xstate_var}; skipping")
                continue
            merged = existing + [xstate_var]
            new_notes = (notes or "") + (
                " | bakken-xstate-wiring: pipe_bakken_xstate outflow added "
                "(latent constituent of EIA inter-PADD measurement; zero numerical effect)"
            )
            cur.execute(
                """
                UPDATE oil_network.variable_assignments
                SET formula_inputs = %s, notes = %s
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (merged, new_notes, SCENARIO, parent_var),
            )
            print(f"  ✓ {parent_var}: +{xstate_var}")
        conn.commit()
    print()
    print("Next: refresh_views.py --structural to refresh v_partition_tree, then re-resolve.")


if __name__ == "__main__":
    main()
