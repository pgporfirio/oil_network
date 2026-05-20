"""Propagate per-grade production AND producer-outflow variables for every US basin.

For each (basin, grade, share) entry in BASIN_GRADE_SHARES:

  (1) Per-grade production at the basin:
        production__{grade}__{basin} = <share> * production__crude__{basin}

  (2) Per-grade outflow per (basin -> downstream) edge, proportional to the
      crude flow on that edge:
        outflow__{grade}__{basin}__{X}
            = (outflow__crude__{basin}__{X} / production__crude__{basin})
              * production__{grade}__{basin}

      And the paired inflow on the downstream node as latent() — mirror
      promotion will fill it from the outflow side.

Closures:
  - per basin: sum_grades production_g = production_crude  (shares sum to 1.0)
  - per (basin, edge X): per-grade outflow shares sum to outflow_crude_to_X
    by construction of the proportional formula
  - per basin: sum over edges of per-grade outflow_g
              = production_g  (since sum over edges of outflow_crude = production_crude)

Affects scenario crude_starter_with_grades only. Idempotent.

Multi-outflow basins (gulf_of_america = 28 downstream, california = 12, etc.)
get proportional allocation across every outflow edge. This is the "perfect
mixing at the basin" assumption: any crude leaving the basin carries the
basin's overall grade composition. Refinery slate decomposition (which would
override this with a slate-based rule per (basin -> refinery, grade)) is the
next refinement; for now the proportional rule is the structural default.

Share sources: typical industry splits per RBN Energy / Argus / EIA basin
notes. Coarse but defensible; would be refined to TS-bound shares (Argus
assay series, EIA petroleum monthly sub-tables) in a serious calibration.
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
        n_flow_vars = 0
        n_flow_assigns = 0

        for basin, shares in BASIN_GRADE_SHARES.items():
            crude_prod_var = f"production__crude__{basin}"
            cur.execute("SELECT 1 FROM oil_network.variables WHERE variable_id = %s", (crude_prod_var,))
            if not cur.fetchone():
                print(f"  SKIP {basin}: no production__crude__{basin} variable exists")
                continue

            # (1) Per-grade production variables + share assignments
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
                """, (SCENARIO, vid, EFFECTIVE_FROM, f"{share} * {crude_prod_var}", [crude_prod_var]))
                n_assigns += 1

            # (2) Per-grade outflow variables on every (basin -> X) edge, with
            #     proportional allocation. Paired inflow on X is latent (mirror-promoted).
            cur.execute("""
                SELECT related_node_id FROM oil_network.variables
                WHERE node_id = %s AND variable_type = 'outflow' AND commodity = 'crude'
            """, (basin,))
            downstream_nodes = [r[0] for r in cur.fetchall()]
            for X in downstream_nodes:
                out_crude_var = f"outflow__crude__{basin}__{X}"
                for grade, _ in shares:
                    prod_grade_var = f"production__{grade}__{basin}"
                    out_grade_vid = f"outflow__{grade}__{basin}__{X}"
                    in_grade_vid  = f"inflow__{grade}__{X}__{basin}"
                    # outflow variable on basin side
                    cur.execute("""
                        INSERT INTO oil_network.variables
                            (variable_id, variable_type, commodity, node_id, related_node_id, attributes)
                        VALUES (%s, 'outflow', %s, %s, %s, '{}')
                        ON CONFLICT (variable_id) DO NOTHING
                    """, (out_grade_vid, grade, basin, X))
                    n_flow_vars += cur.rowcount
                    # paired inflow variable on downstream side
                    cur.execute("""
                        INSERT INTO oil_network.variables
                            (variable_id, variable_type, commodity, node_id, related_node_id, attributes)
                        VALUES (%s, 'inflow', %s, %s, %s, '{}')
                        ON CONFLICT (variable_id) DO NOTHING
                    """, (in_grade_vid, grade, X, basin))
                    n_flow_vars += cur.rowcount
                    # outflow assignment: proportional formula
                    formula = f"({out_crude_var} / {crude_prod_var}) * {prod_grade_var}"
                    cur.execute("""
                        INSERT INTO oil_network.variable_assignments
                            (scenario_id, variable_id, effective_from, timeseries_id, formula,
                             formula_inputs, formula_input_offsets)
                        VALUES (%s, %s, %s, NULL, %s, %s, NULL)
                        ON CONFLICT (scenario_id, variable_id, effective_from) DO UPDATE
                        SET formula = EXCLUDED.formula,
                            formula_inputs = EXCLUDED.formula_inputs,
                            timeseries_id = NULL
                    """, (SCENARIO, out_grade_vid, EFFECTIVE_FROM, formula,
                          [out_crude_var, crude_prod_var, prod_grade_var]))
                    n_flow_assigns += 1
                    # inflow assignment: latent (mirror promotion fills it from the outflow side)
                    cur.execute("""
                        INSERT INTO oil_network.variable_assignments
                            (scenario_id, variable_id, effective_from, timeseries_id, formula,
                             formula_inputs, formula_input_offsets)
                        VALUES (%s, %s, %s, NULL, 'latent()', '{}', NULL)
                        ON CONFLICT (scenario_id, variable_id, effective_from) DO UPDATE
                        SET formula = 'latent()',
                            formula_inputs = '{}',
                            timeseries_id = NULL
                    """, (SCENARIO, in_grade_vid, EFFECTIVE_FROM))
                    n_flow_assigns += 1

        conn.commit()
        print(f"\n[1] created {n_vars} new production variables (vs. existing)")
        print(f"[2] wrote {n_assigns} production-share assignments")
        print(f"[3] created {n_flow_vars} new flow variables (paired outflow+inflow per basin x grade x downstream edge)")
        print(f"[4] wrote {n_flow_assigns} flow assignments (outflow=proportional, inflow=latent)")

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
