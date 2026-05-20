"""Add formula_input_offsets INT[] to variable_assignments and expose it on
v_effective_assignments — enables lagged self-references in the resolver.

Each entry in `formula_input_offsets` is the date-shift (in months) for the
corresponding entry in `formula_inputs`. Default 0 (= same date as the
variable being evaluated). Negative values reference past dates (e.g., -1 =
previous month), enabling inventory recursion `S(t) = S(t-1) + ...`.

Missing or NULL offsets are treated as all-zero.

Idempotent: column add is IF NOT EXISTS; view is CREATE OR REPLACE.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

SQL = """
ALTER TABLE oil_network.variable_assignments
    ADD COLUMN IF NOT EXISTS formula_input_offsets INT[];

CREATE OR REPLACE VIEW oil_network.v_effective_assignments AS
SELECT s.scenario_id, v.variable_id, v.node_id, v.variable_type, v.commodity,
       v.related_node_id, n.node_type,
       COALESCE(va.effective_from, '1900-01-01'::date) AS effective_from,
       CASE WHEN va.effective_from IS NOT NULL THEN va.timeseries_id ELSE NULL END AS timeseries_id,
       CASE WHEN va.effective_from IS NOT NULL THEN va.formula
            ELSE COALESCE(ntdf.default_formula, 'latent()') END AS formula,
       CASE WHEN va.effective_from IS NOT NULL THEN COALESCE(va.formula_inputs, ARRAY[]::text[])
            ELSE ARRAY[]::text[] END AS formula_inputs,
       CASE WHEN va.effective_from IS NOT NULL THEN 'override'
            WHEN ntdf.node_type IS NOT NULL THEN 'default'
            ELSE 'fallback' END AS assignment_source,
       va.notes,
       CASE WHEN va.effective_from IS NOT NULL THEN COALESCE(va.formula_input_offsets, ARRAY[]::int[])
            ELSE ARRAY[]::int[] END AS formula_input_offsets
FROM oil_network.scenarios s
CROSS JOIN oil_network.variables v
JOIN oil_network.nodes n ON n.node_id = v.node_id
LEFT JOIN LATERAL (
    SELECT va_1.timeseries_id, va_1.formula, va_1.formula_inputs,
           va_1.formula_input_offsets, va_1.effective_from, va_1.notes
    FROM oil_network.variable_assignments va_1
    WHERE va_1.scenario_id = s.scenario_id AND va_1.variable_id = v.variable_id
    ORDER BY va_1.effective_from DESC LIMIT 1
) va ON true
LEFT JOIN oil_network.node_type_default_formulas ntdf
    ON ntdf.node_type = n.node_type AND ntdf.variable_type = v.variable_type
   AND (ntdf.commodity IS NULL OR ntdf.commodity = v.commodity);
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(SQL); conn.commit()
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='oil_network' AND table_name='variable_assignments' "
                    "AND column_name='formula_input_offsets'")
        print(f"formula_input_offsets column present: {cur.fetchone() is not None}")
        cur.execute("SELECT COUNT(*) FROM oil_network.v_effective_assignments")
        print(f"v_effective_assignments rebuilt: {cur.fetchone()[0]:,} rows")


if __name__ == "__main__":
    main()
