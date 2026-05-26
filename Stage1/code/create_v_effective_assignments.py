"""Create oil_network.v_effective_assignments — the resolver's single read source.

Layers the resolution recipe per (scenario, variable):

  1. variable_assignments  (per-scenario override)         -> 'override'
  2. node_type_default_formulas (structural type default)  -> 'default'
  3. 'latent()' fallback (relational or missing-type)      -> 'fallback'

The view is keyed on (scenario_id, variable_id). Every variable on every scenario
yields exactly one row — the recipe the resolver will use. Adding a new scenario
inherits all defaults for free; only deliberate overrides need a row in
variable_assignments.

Idempotent: CREATE OR REPLACE.
"""
from __future__ import annotations

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
CREATE OR REPLACE VIEW oil_network.v_effective_assignments AS
SELECT
    s.scenario_id,
    v.variable_id,
    v.node_id,
    v.variable_type,
    v.commodity,
    v.related_node_id,
    n.node_type,
    COALESCE(va.effective_from, '1900-01-01'::date)             AS effective_from,
    va.timeseries_id,
    COALESCE(va.formula, ntdf.default_formula, 'latent()')      AS formula,
    COALESCE(va.formula_inputs, ARRAY[]::TEXT[])                AS formula_inputs,
    CASE
        WHEN va.effective_from IS NOT NULL THEN 'override'
        WHEN ntdf.node_type IS NOT NULL    THEN 'default'
        ELSE                                    'fallback'
    END                                                         AS assignment_source,
    va.notes
FROM oil_network.scenarios s
CROSS JOIN oil_network.variables v
JOIN oil_network.nodes n ON n.node_id = v.node_id
LEFT JOIN LATERAL (
    -- Most-recent override for this (scenario, variable). Multi-row would mean
    -- temporal versioning (effective_from), which the starter scenario does not use
    -- yet — but the LATERAL keeps the view forward-compatible.
    SELECT timeseries_id, formula, formula_inputs, effective_from, notes
    FROM oil_network.variable_assignments va
    WHERE va.scenario_id = s.scenario_id
      AND va.variable_id = v.variable_id
    ORDER BY va.effective_from DESC
    LIMIT 1
) va ON TRUE
LEFT JOIN oil_network.node_type_default_formulas ntdf
    ON ntdf.node_type     = n.node_type
   AND ntdf.variable_type = v.variable_type
   AND (ntdf.commodity IS NULL OR ntdf.commodity = v.commodity);

COMMENT ON VIEW oil_network.v_effective_assignments IS
'Effective resolution recipe per (scenario, variable): variable_assignments override > node_type_default_formulas default > latent() fallback. assignment_source column tags which layer fired. Used by resolve_scenario.py.';
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
        # Quick sanity check on the starter scenario
        cur.execute("""
            SELECT assignment_source, COUNT(*) AS n
            FROM oil_network.v_effective_assignments
            WHERE scenario_id = 'starter_us_crude_2015_2025'
            GROUP BY assignment_source
            ORDER BY assignment_source
        """)
        print("Coverage breakdown for starter_us_crude_2015_2025:")
        for src, n in cur.fetchall():
            print(f"  {src:<10}  {n:>5} rows")

        cur.execute("""
            SELECT v.variable_type, ea.assignment_source, ea.formula, COUNT(*) AS n
            FROM oil_network.v_effective_assignments ea
            JOIN oil_network.variables v USING (variable_id)
            WHERE ea.scenario_id = 'starter_us_crude_2015_2025'
              AND ea.assignment_source = 'default'
            GROUP BY v.variable_type, ea.assignment_source, ea.formula
            ORDER BY v.variable_type, ea.formula
        """)
        print("\nDefaults firing in starter (where assign_formulas would have written the same thing):")
        for vt, src, formula, n in cur.fetchall():
            print(f"  {vt:<18} {formula:<20} {n:>4} rows")


if __name__ == "__main__":
    main()
