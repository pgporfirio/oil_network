"""DEMO ONLY: add three grade-share production variables for Permian-TX.

Inserts the per-grade variables for permian_tx in scenario
crude_starter_with_grades, each bound to a scalar share of the existing
crude-level production:

  production__wti_midland__permian_tx       = 0.70 * P_crude_permian_tx
  production__wtl__permian_tx               = 0.20 * P_crude_permian_tx
  production__permian_condensate__permian_tx = 0.10 * P_crude_permian_tx

Closure: 0.70 + 0.20 + 0.10 = 1.00 so per-grade sums back to crude.

Idempotent. This is a temporary demonstration so the balance-hierarchy HTML
has something to show under the crude row; the real propagator will do this
for every (basin x grade) pair using share variables (TS or constant) of
its own. Safe to delete once the propagator lands.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "crude_starter_with_grades"
BASIN = "permian_tx"
CRUDE_VAR = f"production__crude__{BASIN}"

SHARES = [
    ("wti_midland", 0.70),
    ("wtl", 0.20),
    ("permian_condensate", 0.10),
]


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        for grade, share in SHARES:
            vid = f"production__{grade}__{BASIN}"
            cur.execute("""
                INSERT INTO oil_network.variables
                    (variable_id, variable_type, commodity, node_id, related_node_id, attributes)
                VALUES (%s, 'production', %s, %s, NULL, '{}')
                ON CONFLICT (variable_id) DO NOTHING
            """, (vid, grade, BASIN))
            cur.execute("""
                INSERT INTO oil_network.variable_assignments
                    (scenario_id, variable_id, effective_from, timeseries_id, formula,
                     formula_inputs, formula_input_offsets)
                VALUES (%s, %s, '2015-01-01', NULL, %s, %s, NULL)
                ON CONFLICT (scenario_id, variable_id, effective_from) DO UPDATE
                SET formula = EXCLUDED.formula,
                    formula_inputs = EXCLUDED.formula_inputs,
                    timeseries_id = NULL
            """, (SCENARIO, vid, f"{share} * {CRUDE_VAR}", [CRUDE_VAR]))
            print(f"  inserted {vid} = {share} * {CRUDE_VAR}")
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM oil_network.variables WHERE node_id=%s", (BASIN,))
        print(f"\nvariables on {BASIN}: {cur.fetchone()[0]}")


if __name__ == "__main__":
    main()
