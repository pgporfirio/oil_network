"""Clone starter_us_crude_2015_2025 -> crude_starter_with_grades.

Copies the scenarios row and all variable_assignments rows under the new
scenario_id, leaving everything else (variables, commodities, hierarchy,
v_node_routes, ...) untouched. The clone is structurally identical to the
starter — same 975 assignments, all binding commodity=crude.

This is the starting point for the grade-decomposition workstream: per-grade
overrides land in this scenario, the starter stays clean as the
single-commodity reference. Idempotent.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SRC = "starter_us_crude_2015_2025"
DST = "crude_starter_with_grades"

SQL = f"""
INSERT INTO oil_network.scenarios
  (scenario_id, graph_id, name, description, attributes,
   created_at, time_range_start, time_range_end, frequency, pipeline_layer_note)
SELECT
  '{DST}', graph_id,
  'Crude starter with grades',
  'Clone of {SRC} as the working scenario for per-grade decomposition. '
  'All assignments inherit commodity=crude from the parent; per-grade '
  'overrides are layered on top.',
  attributes || jsonb_build_object('cloned_from', '{SRC}'),
  NOW(), time_range_start, time_range_end, frequency, pipeline_layer_note
FROM oil_network.scenarios WHERE scenario_id = '{SRC}'
ON CONFLICT (scenario_id) DO NOTHING;

INSERT INTO oil_network.variable_assignments
  (scenario_id, variable_id, effective_from, timeseries_id, formula, formula_inputs, notes)
SELECT '{DST}', variable_id, effective_from, timeseries_id, formula, formula_inputs, notes
FROM oil_network.variable_assignments WHERE scenario_id = '{SRC}'
ON CONFLICT DO NOTHING;
"""


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(SQL); conn.commit()
        cur.execute("SELECT scenario_id, name FROM oil_network.scenarios WHERE scenario_id = %s", (DST,))
        s = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM oil_network.variable_assignments WHERE scenario_id = %s", (DST,))
        n = cur.fetchone()[0]
        print(f"scenario: {s[0]}  ({s[1]})")
        print(f"variable_assignments: {n:,}")


if __name__ == "__main__":
    main()
