"""Declare the inter-PADD aggregate outflow edges as sum-of-pipelines.

This is the Principle 2.11 (latent allocation at junctions) wiring at the
inter-PADD scale: per-pipe outflows that carry crude across PADD boundaries
are jointly constrained by the corresponding aggregate observation. We
document the relationship by setting `formula_inputs` on the aggregate.

Data change only — the resolver requires no code change:
  * Aggregate stays TS-bound to its `eia:combined_inter_padd_*` series, so
    the resolver's Rule 1 (observed) fires first and the value is unchanged.
  * `formula_inputs` is metadata that the consistency view, future resolver
    extensions, and downstream UIs can use to walk the pipe decomposition.

Mapped corridors (3 of the 10 inter-PADD aggregate outflow edges):
  P2 -> P3 :  pipe_capline -> st_james,
              pipe_marketlink -> nederland,
              pipe_seaway -> houston   (south-bound direction)
  P3 -> P2 :  pipe_basin -> cushing,
              pipe_seaway -> cushing   (north-bound direction)
  P4 -> P2 :  pipe_pony_express -> cushing

Unmapped corridors (kept with empty formula_inputs so the missing-pipe-data
signal is honest):
  P1 <-> {P2, P3}      : tanker movements only, no modeled pipe
  P2 -> P4             : no reverse Pony Express modeled
  P3 -> {P4, P5}       : rare crude movements, mostly tanker / rail
  P5 -> P3             : same

Idempotent: re-running UPDATEs to the same value.
"""
from __future__ import annotations

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

# corridor -> list of pipe-outflow variable_ids contributing to it
CORRIDOR_PIPES: dict[tuple[str, str], list[str]] = {
    ("padd2_view", "padd3_view"): [
        "outflow__crude__pipe_capline__st_james_hub",
        "outflow__crude__pipe_marketlink__nederland_hub",
        "outflow__crude__pipe_seaway__houston_hub",      # south-bound seaway
    ],
    ("padd3_view", "padd2_view"): [
        "outflow__crude__pipe_basin__cushing_hub",
        "outflow__crude__pipe_seaway__cushing_hub",      # north-bound seaway
    ],
    ("padd4_view", "padd2_view"): [
        "outflow__crude__pipe_pony_express__cushing_hub",
    ],
}


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # Sanity: verify each constituent variable_id exists in oil_network.variables
        flat = sorted({v for vs in CORRIDOR_PIPES.values() for v in vs})
        cur.execute(
            "SELECT variable_id FROM oil_network.variables WHERE variable_id = ANY(%s)",
            (flat,),
        )
        found = {r[0] for r in cur.fetchall()}
        missing = [v for v in flat if v not in found]
        if missing:
            print("ERROR: the following constituent variable_ids do not exist:")
            for m in missing:
                print(f"  - {m}")
            raise SystemExit(1)

        # Also verify each aggregate (outflow__crude__<src>__<dst>) exists and is TS-bound
        agg_ids = [f"outflow__crude__{src}__{dst}" for (src, dst) in CORRIDOR_PIPES]
        cur.execute(
            """
            SELECT v.variable_id, va.timeseries_id, va.formula, cardinality(va.formula_inputs)
            FROM oil_network.variables v
            JOIN oil_network.variable_assignments va USING (variable_id)
            WHERE va.scenario_id = %s AND v.variable_id = ANY(%s)
            """,
            (SCENARIO, agg_ids),
        )
        agg_state = {r[0]: r for r in cur.fetchall()}
        for vid in agg_ids:
            if vid not in agg_state:
                print(f"ERROR: aggregate {vid!r} has no variable_assignment for scenario {SCENARIO!r}")
                raise SystemExit(1)

        print("=== Before ===")
        for vid in agg_ids:
            ts, formula, n_inputs = agg_state[vid][1], agg_state[vid][2], agg_state[vid][3]
            print(f"  {vid:55s}  ts={ts!r:42s}  formula={formula!r:5s}  n_inputs={n_inputs}")

        # Apply UPDATEs: set formula_inputs to the declared pipe list.
        # Keep formula = '' (it was already empty) and timeseries_id unchanged.
        n_updated = 0
        for (src, dst), pipes in CORRIDOR_PIPES.items():
            vid = f"outflow__crude__{src}__{dst}"
            cur.execute(
                """
                UPDATE oil_network.variable_assignments
                SET formula_inputs = %s,
                    notes = CASE
                              WHEN notes IS NULL OR notes = ''
                                THEN %s
                              ELSE notes || '; ' || %s
                            END
                WHERE scenario_id = %s AND variable_id = %s
                """,
                (
                    pipes,
                    f"Inter-PADD aggregate constrained by sum of {len(pipes)} pipe outflow(s) (latent split, Principle 2.11)",
                    f"Inter-PADD aggregate constrained by sum of {len(pipes)} pipe outflow(s) (latent split, Principle 2.11)",
                    SCENARIO,
                    vid,
                ),
            )
            n_updated += cur.rowcount
        conn.commit()
        print(f"\n{n_updated} aggregate row(s) updated\n")

        # Verify
        cur.execute(
            """
            SELECT v.variable_id, va.timeseries_id, va.formula,
                   va.formula_inputs, va.notes
            FROM oil_network.variables v
            JOIN oil_network.variable_assignments va USING (variable_id)
            WHERE va.scenario_id = %s AND v.variable_id = ANY(%s)
            ORDER BY v.variable_id
            """,
            (SCENARIO, agg_ids),
        )
        print("=== After ===")
        for r in cur.fetchall():
            inputs = r[3] or []
            print(f"  {r[0]}")
            print(f"    ts={r[1]!r}  formula={r[2]!r}")
            for x in inputs:
                print(f"    + {x}")
            print(f"    notes: {(r[4] or '')[:100]}")


if __name__ == "__main__":
    main()
