"""Propagate per-grade production variables for every US basin.

For each (basin, grade, share) entry in BASIN_GRADE_SHARES, creates
production__{grade}__{basin} as a scalar share of the existing
production__crude__{basin}:

    production__wti_midland__permian_tx = 0.70 * production__crude__permian_tx
    production__wtl__permian_tx         = 0.20 * production__crude__permian_tx
    production__permian_condensate__permian_tx = 0.10 * production__crude__permian_tx
    ... etc for each basin

Shares per basin sum to 1.0 so per-grade values reconcile back to the
crude-level production (closure verified by the migration's final report).

Affects scenario crude_starter_with_grades only — the starter scenario
stays single-commodity. Idempotent via ON CONFLICT.

This is v1 of the propagator: producer-level grade decomposition only.
Downstream propagation (per-edge inflows / outflows, per-grade inventory
dynamics, refinery slate consumption) is the next iteration once the LP
allocator is in place. Today the balance-hierarchy HTML's Composition
panel will show grade breakdowns at producer nodes; pass-through and
storage nodes will show only crude.

Share sources: typical industry splits per RBN Energy / Argus / EIA
basin notes. Numbers are coarse — fine for a structural demo, would be
refined to TS-bound shares (Argus assay series, EIA petroleum monthly
sub-tables) in a serious calibration pass.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "crude_starter_with_grades"
EFFECTIVE_FROM = "2015-01-01"


# Each basin -> list of (grade, share). Shares per basin must sum to 1.0.
BASIN_GRADE_SHARES = {
    # Permian sub-basins
    "permian_tx":  [("wti_midland", 0.70), ("wtl", 0.20), ("permian_condensate", 0.10)],
    "permian_nm":  [("wti_midland", 0.80), ("wtl", 0.15), ("permian_condensate", 0.05)],
    # Bakken sub-basins
    "bakken_nd":   [("bakken_light", 1.00)],
    "bakken_mt":   [("bakken_light", 1.00)],
    # Eagle Ford
    "eagle_ford_tx": [("eagle_ford_light", 0.60), ("eagle_ford_condensate", 0.40)],
    # Gulf of America (offshore mix; named GoM grades + LLS for Louisiana-adjacent)
    "gulf_of_america": [("mars", 0.40), ("thunder_horse", 0.25), ("poseidon", 0.15),
                        ("southern_green_canyon", 0.10), ("lls", 0.10)],
    # Alaska — single grade
    "alaska_north_slope": [("ans", 1.00)],
    # California — heavy crude sub-grades
    "california_conventional": [("kern_heavy", 0.60), ("midway_sunset", 0.40)],
    # Other conventional states
    "oklahoma_conventional": [("oklahoma_sweet", 1.00)],
    "colorado_conventional": [("niobrara_sweet", 1.00)],
    "wyoming_conventional":  [("wyoming_sweet", 0.70), ("wyoming_asphaltic", 0.30)],
    # State residuals (small volumes; lump into nearest representative grade)
    "montana_other": [("bakken_light", 1.00)],   # mostly Cedar Creek anticline
    "texas_other":   [("wti_midland", 1.00)],     # mostly Permian-tail / Anadarko-extension
}


def main():
    # Sanity: shares must sum to 1.0 per basin
    for basin, shares in BASIN_GRADE_SHARES.items():
        total = sum(s for _, s in shares)
        assert abs(total - 1.0) < 1e-9, f"{basin}: shares sum to {total}, not 1.0"

    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        n_vars = 0
        n_assigns = 0
        for basin, shares in BASIN_GRADE_SHARES.items():
            crude_var = f"production__crude__{basin}"
            cur.execute("SELECT 1 FROM oil_network.variables WHERE variable_id = %s", (crude_var,))
            if not cur.fetchone():
                print(f"  SKIP {basin}: no production__crude__{basin} variable exists")
                continue
            for grade, share in shares:
                vid = f"production__{grade}__{basin}"
                cur.execute("""
                    INSERT INTO oil_network.variables
                        (variable_id, variable_type, commodity, node_id, related_node_id, attributes)
                    VALUES (%s, 'production', %s, %s, NULL, '{}')
                    ON CONFLICT (variable_id) DO NOTHING
                """, (vid, grade, basin))
                n_vars += cur.rowcount
                cur.execute("""
                    INSERT INTO oil_network.variable_assignments
                        (scenario_id, variable_id, effective_from, timeseries_id, formula,
                         formula_inputs, formula_input_offsets)
                    VALUES (%s, %s, %s, NULL, %s, %s, NULL)
                    ON CONFLICT (scenario_id, variable_id, effective_from) DO UPDATE
                    SET formula = EXCLUDED.formula,
                        formula_inputs = EXCLUDED.formula_inputs,
                        timeseries_id = NULL
                """, (SCENARIO, vid, EFFECTIVE_FROM, f"{share} * {crude_var}", [crude_var]))
                n_assigns += 1
        conn.commit()
        print(f"\n[1] created {n_vars} new variables (vs. existing)")
        print(f"[2] wrote {n_assigns} share assignments to {SCENARIO}")

        # Sanity: total per-grade variables on these basins
        cur.execute("""
            SELECT v.node_id, COUNT(*) AS n_grades
            FROM oil_network.variables v
            WHERE v.variable_type = 'production' AND v.commodity != 'crude'
              AND v.node_id = ANY(%s)
            GROUP BY v.node_id ORDER BY v.node_id
        """, (list(BASIN_GRADE_SHARES.keys()),))
        print(f"\n[3] per-grade production variables now on each basin:")
        for node, n in cur.fetchall():
            print(f"  {node:30s}  {n} grades")


if __name__ == "__main__":
    main()
