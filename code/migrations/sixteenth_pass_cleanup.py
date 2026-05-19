"""Sixteenth-pass cleanup: tidy two leftovers from the twelfth pass.

Surfaced during a post-fifteenth-pass HTML sanity check.

(A) `node_type_default_formulas` still carries the legacy label
    'sum_over_children' for `observational_aggregate.production`. The
    twelfth pass migrated `variable_assignments` rows but left the defaults
    table untouched. Result: any TS-bound observational_aggregate that
    relies on the default formula_inputs shows up with the obsolete formula
    label in v_effective_assignments. Cosmetic today (the resolver dispatches
    on TS first), but wrong on principle and visible to downstream consumers.

(B) `v_effective_assignments` does `COALESCE(va.formula, ntdf.default_formula,
    'latent()')` — which means when a row exists in `variable_assignments`
    with `timeseries_id IS NOT NULL` and `formula IS NULL` (TS-bound, no
    formula by design), the COALESCE falls through to the default formula.
    The override is being shadowed by the default. Correct semantics: if
    there is an override row at all, take its values verbatim — even when
    `formula` is NULL. Defaults only apply when there is no override row.

Both fixed here. Idempotent.
"""
from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# ---------------------------------------------------------------------------
# (A) Migrate the defaults table
# ---------------------------------------------------------------------------

MIGRATE_DEFAULTS = """
UPDATE oil_network.node_type_default_formulas
SET default_formula = 'sum',
    description = COALESCE(NULLIF(description,''),'')
                  || CASE WHEN COALESCE(description,'')='' THEN '' ELSE ' | ' END
                  || 'sixteenth-pass: canonical label is sum'
WHERE default_formula IN ('sum_over_children', 'sum_over_outflows', 'sum_same_type')
RETURNING node_type, variable_type, default_formula;
"""


# ---------------------------------------------------------------------------
# (B) Fix v_effective_assignments
# ---------------------------------------------------------------------------

# Replace the COALESCE(va.formula, ntdf.default_formula, ...) with a CASE that
# honours the override row's semantics:
#   - if an override row exists (va.effective_from IS NOT NULL):
#         use va's TS / formula / formula_inputs verbatim, even if formula is NULL
#         (TS-bound rows legitimately carry NULL formula)
#   - else: apply the default formula
# This requires CREATE OR REPLACE; we DROP first because dependent views
# (v_partition_tree, v_node_status, etc.) reference it via SELECT and need
# CASCADE.

V_EFFECTIVE_ASSIGNMENTS_NEW = """
CREATE OR REPLACE VIEW oil_network.v_effective_assignments AS
SELECT s.scenario_id,
       v.variable_id,
       v.node_id,
       v.variable_type,
       v.commodity,
       v.related_node_id,
       n.node_type,
       COALESCE(va.effective_from, '1900-01-01'::date)        AS effective_from,
       CASE WHEN va.effective_from IS NOT NULL
            THEN va.timeseries_id
            ELSE NULL
       END                                                    AS timeseries_id,
       CASE WHEN va.effective_from IS NOT NULL
            THEN va.formula
            ELSE COALESCE(ntdf.default_formula, 'latent()')
       END                                                    AS formula,
       CASE WHEN va.effective_from IS NOT NULL
            THEN COALESCE(va.formula_inputs, ARRAY[]::text[])
            ELSE ARRAY[]::text[]
       END                                                    AS formula_inputs,
       CASE WHEN va.effective_from IS NOT NULL THEN 'override'
            WHEN ntdf.node_type IS NOT NULL    THEN 'default'
                                                ELSE 'fallback'
       END                                                    AS assignment_source,
       va.notes
FROM oil_network.scenarios s
CROSS JOIN oil_network.variables v
JOIN oil_network.nodes n ON n.node_id = v.node_id
LEFT JOIN LATERAL (
    SELECT va_1.timeseries_id, va_1.formula, va_1.formula_inputs,
           va_1.effective_from, va_1.notes
    FROM oil_network.variable_assignments va_1
    WHERE va_1.scenario_id = s.scenario_id
      AND va_1.variable_id = v.variable_id
    ORDER BY va_1.effective_from DESC
    LIMIT 1
) va ON true
LEFT JOIN oil_network.node_type_default_formulas ntdf
    ON ntdf.node_type     = n.node_type
   AND ntdf.variable_type = v.variable_type
   AND (ntdf.commodity IS NULL OR ntdf.commodity = v.commodity);

COMMENT ON VIEW oil_network.v_effective_assignments IS
'For each (scenario, variable) returns the effective recipe. If an override '
'row exists in variable_assignments under this scenario, its values are '
'taken verbatim (even when formula is NULL — TS-bound rows legitimately '
'carry NULL formula). Otherwise the node_type_default_formulas default '
'applies. Sixteenth-pass refactor of the COALESCE chain that previously '
'bled defaults into TS-bound override rows.';
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # (A)
        print("=== A. Migrate node_type_default_formulas ===")
        cur.execute(MIGRATE_DEFAULTS)
        rows = cur.fetchall()
        if rows:
            for r in rows:
                print(f"  ✓ {r[0]:30s} {r[1]:15s} -> {r[2]}")
        else:
            print("  · no rows to migrate (already at 'sum')")

        # (B)
        print("\n=== B. Rebuild v_effective_assignments with correct override semantics ===")
        # The view is regular (not materialised); CREATE OR REPLACE works
        # only if column list and types match. Use CREATE OR REPLACE; if it
        # rejects because dependent views select * from us, fall back to
        # drop-and-recreate-cascade. Our materialised views do JOIN against
        # v_effective_assignments so they should survive a definition change
        # that keeps the same column shape.
        try:
            cur.execute(V_EFFECTIVE_ASSIGNMENTS_NEW)
            print("  ✓ v_effective_assignments rebuilt via CREATE OR REPLACE")
        except psycopg2.errors.InvalidTableDefinition as e:
            print(f"  ! CREATE OR REPLACE failed: {e}")
            print("  ! falling back to DROP CASCADE + recreate; downstream materialised views")
            print("    will need to be rebuilt via thirteenth_pass_views.py afterwards.")
            conn.rollback()
            with conn.cursor() as cur2:
                cur2.execute("DROP VIEW oil_network.v_effective_assignments CASCADE")
                cur2.execute(V_EFFECTIVE_ASSIGNMENTS_NEW)
        conn.commit()
        print()

        # Verify
        cur.execute("""
            SELECT variable_id, timeseries_id, formula, assignment_source
            FROM oil_network.v_effective_assignments
            WHERE scenario_id = 'starter_us_crude_2015_2025'
              AND variable_id IN ('production__crude__bakken',
                                  'production__crude__permian')
        """)
        print("Sanity check: TS-bound observational aggregates no longer bleed defaults")
        for r in cur.fetchall():
            print(f"  {r[0]:35s} ts={r[1]:18s}  formula={r[2]!r:18s}  source={r[3]}")

    print("\nAll fixed. Next: rebuild thirteenth_pass_views (the materialised views read")
    print("from v_effective_assignments) and regenerate the HTMLs.")


if __name__ == "__main__":
    main()
