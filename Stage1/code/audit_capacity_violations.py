"""Post-resolution audit: flag every (variable, date) value that violates its
effective capacity constraint.

Reads `v_effective_constraints` (which joins asset_capacities + variable_constraints
overlay) and compares each resolved value against [min_value, max_value]. Emits
warnings for any breach. Does NOT block the run — a TS observation outside its
declared capacity is informational, not fatal (the TS may itself be the issue,
or the capacity may need updating).

Exit code: 0 always (audit is advisory). Violations count + per-violation
detail go to stdout.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # The most recent applicable constraint per (scenario, variable, kind)
        # is the row with the largest effective_from <= the date we're testing.
        # We compare against every resolved (variable, date) pair.
        cur.execute("""
            WITH effective AS (
                SELECT DISTINCT ON (scenario_id, variable_id, capacity_kind)
                       scenario_id, variable_id, capacity_kind, effective_from,
                       min_value, max_value, unit, source, layer
                FROM oil_network.v_effective_constraints
                WHERE scenario_id = %s
                ORDER BY scenario_id, variable_id, capacity_kind, effective_from DESC
            ),
            violations AS (
                SELECT srv.scenario_id, srv.variable_id, srv.observation_date,
                       srv.value, srv.source AS resolve_source,
                       e.min_value, e.max_value, e.unit, e.layer, e.capacity_kind
                FROM oil_network.scenario_resolved_values srv
                JOIN effective e
                  ON e.scenario_id = srv.scenario_id
                 AND e.variable_id = srv.variable_id
                 AND e.effective_from <= srv.observation_date
                WHERE srv.scenario_id = %s
                  AND srv.value IS NOT NULL
                  AND (   (e.max_value IS NOT NULL AND srv.value > e.max_value)
                       OR (e.min_value IS NOT NULL AND srv.value < e.min_value))
            )
            SELECT count(*) FROM violations
        """, (SCENARIO, SCENARIO))
        n_violations = cur.fetchone()[0]

        cur.execute("""
            WITH effective AS (
                SELECT DISTINCT ON (scenario_id, variable_id, capacity_kind)
                       scenario_id, variable_id, capacity_kind, effective_from,
                       min_value, max_value, unit, source, layer
                FROM oil_network.v_effective_constraints
                WHERE scenario_id = %s
                ORDER BY scenario_id, variable_id, capacity_kind, effective_from DESC
            )
            SELECT count(*) AS n_constrained_variables,
                   count(*) FILTER (WHERE max_value IS NOT NULL) AS n_with_max,
                   count(*) FILTER (WHERE min_value IS NOT NULL AND min_value > 0) AS n_with_min
            FROM effective
        """, (SCENARIO,))
        n_constrained, n_max, n_min = cur.fetchone()

        print(f"=== Capacity-violation audit  (scenario: {SCENARIO}) ===")
        print(f"  variables with active capacity constraint: {n_constrained}")
        print(f"    of which max-bounded:                    {n_max}")
        print(f"    of which min-bounded (>0):               {n_min}")
        print(f"  resolved-value rows violating a bound:     {n_violations}")
        print()

        if n_violations == 0:
            print("No violations — every observed/derived value respects its capacity.")
            return

        # Detail: show top-20 violations grouped by variable
        cur.execute("""
            WITH effective AS (
                SELECT DISTINCT ON (scenario_id, variable_id, capacity_kind)
                       scenario_id, variable_id, capacity_kind, effective_from,
                       min_value, max_value, unit, source, layer
                FROM oil_network.v_effective_constraints
                WHERE scenario_id = %s
                ORDER BY scenario_id, variable_id, capacity_kind, effective_from DESC
            ),
            violations AS (
                SELECT srv.variable_id, srv.observation_date,
                       srv.value, srv.source AS resolve_source,
                       e.min_value, e.max_value, e.unit, e.layer, e.capacity_kind,
                       CASE WHEN e.max_value IS NOT NULL AND srv.value > e.max_value
                            THEN srv.value - e.max_value
                            WHEN e.min_value IS NOT NULL AND srv.value < e.min_value
                            THEN e.min_value - srv.value
                            ELSE NULL END AS overshoot
                FROM oil_network.scenario_resolved_values srv
                JOIN effective e
                  ON e.scenario_id = srv.scenario_id
                 AND e.variable_id = srv.variable_id
                 AND e.effective_from <= srv.observation_date
                WHERE srv.scenario_id = %s
                  AND srv.value IS NOT NULL
                  AND (   (e.max_value IS NOT NULL AND srv.value > e.max_value)
                       OR (e.min_value IS NOT NULL AND srv.value < e.min_value))
            )
            SELECT variable_id, count(*) AS n_breaches,
                   max(overshoot) AS worst_overshoot,
                   min(observation_date), max(observation_date),
                   min(unit), min(layer), min(resolve_source)
            FROM violations
            GROUP BY variable_id
            ORDER BY worst_overshoot DESC NULLS LAST
            LIMIT 20
        """, (SCENARIO, SCENARIO))
        rows = cur.fetchall()
        print(f"{'variable_id':60s} {'n':>5s} {'worst':>9s} {'unit':>5s} {'layer':>20s} {'src':>10s}")
        for r in rows:
            print(f"  {r[0]:58s} {r[1]:>5d} {r[2]:>9.2f} {r[5]:>5s} {r[6]:>20s} {r[7]:>10s}")
        print()
        print(f"(Top 20 by worst overshoot; {n_violations} total breach rows across all variables.)")
        print("Audit is advisory — these are warnings, not failures.")


if __name__ == "__main__":
    main()
