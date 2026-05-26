"""Build the `refineries` schema.

A separate schema from `oil_network` that holds reference data about which
crude grades each US refinery is capable of processing. The mapping is fed
from `config/refinery_grades.json` by `code/load_refinery_grades.py` — the
same JSON-as-seed pattern used for the main asset graph.

Objects created (idempotent):

  - SCHEMA  refineries
  - TABLE   refineries.refinery_grade_assignments
        (refinery_id, commodity, is_primary, source, notes)
        FKs to oil_network.assets and oil_network.commodities so the
        mapping cannot drift out of sync with the canonical refinery list
        or grade registry.
  - VIEW    refineries.v_refinery_grades
        Join of oil_network.assets (refinery rows) + the assignments table
        + oil_network.commodities. One row per (refinery, grade) with all
        the cross-schema metadata pre-joined for downstream readers.
  - VIEW    refineries.v_refinery_grade_audit
        Cross-checks each refinery's assigned grades against its existing
        `preferred_slate`, `nelson_complexity_index`, and `has_coker`
        attributes from oil_network.assets. Flags slate/grade mismatches
        as advisory diagnostics — does not block.

Plain views rather than materialised: the assignment table is small
(~75 refineries x ~5 grades each) and the data changes rarely, so the
view's recompute cost is negligible and we avoid a refresh dance.

Re-running this script is a no-op. Run via:

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\build_refineries_schema.py
"""
from __future__ import annotations

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


DDL = r"""
CREATE SCHEMA IF NOT EXISTS refineries;

COMMENT ON SCHEMA refineries IS
    'Reference data about US refineries: which crude grades each refinery is '
    'capable of processing. Populated from config/refinery_grades.json by '
    'code/load_refinery_grades.py. The canonical refinery list lives in '
    'oil_network.assets; this schema only stores the grade mapping.';

CREATE TABLE IF NOT EXISTS refineries.refinery_grade_assignments (
    refinery_id  TEXT NOT NULL
                 REFERENCES oil_network.assets(asset_id) ON DELETE CASCADE,
    commodity    TEXT NOT NULL
                 REFERENCES oil_network.commodities(commodity) ON DELETE RESTRICT,
    is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
    source       TEXT,
    notes        TEXT,
    PRIMARY KEY (refinery_id, commodity)
);

CREATE INDEX IF NOT EXISTS idx_rga_refinery
    ON refineries.refinery_grade_assignments (refinery_id);
CREATE INDEX IF NOT EXISTS idx_rga_commodity
    ON refineries.refinery_grade_assignments (commodity);

COMMENT ON COLUMN refineries.refinery_grade_assignments.is_primary IS
    'TRUE when the grade is a top-3 slate component for this refinery.';
COMMENT ON COLUMN refineries.refinery_grade_assignments.source IS
    'Provenance label: company_disclosure / industry_report / web_research / '
    'inferred_from_config.';

-- View: refinery + grade + all the cross-schema metadata pre-joined.
CREATE OR REPLACE VIEW refineries.v_refinery_grades AS
SELECT
    a.asset_id                                   AS refinery_id,
    a.name                                       AS refinery_name,
    a.attributes->'configuration'->>'site'       AS site,
    a.attributes->'configuration'->>'operator'   AS operator,
    (a.attributes->'configuration'->>'capacity_bd')::NUMERIC AS capacity_bd,
    (a.attributes->'configuration'->>'nelson_complexity_index')::NUMERIC AS nci,
    a.attributes->'configuration'->>'preferred_slate' AS preferred_slate,
    (a.attributes->'configuration'->>'has_coker')::BOOLEAN AS has_coker,
    (a.attributes->'configuration'->>'has_hydrocracker')::BOOLEAN AS has_hydrocracker,
    a.attributes->'configuration'->>'rdist_label' AS rdist_label,
    a.attributes->'configuration'->>'duoarea_code' AS duoarea_code,
    rga.commodity,
    c.description     AS commodity_description,
    c.sweet_sour,
    c.density_class,
    c.api_gravity_min,
    c.api_gravity_max,
    c.sulfur_pct_min,
    c.sulfur_pct_max,
    c.region          AS commodity_region,
    c.typical_basin,
    rga.is_primary,
    rga.source,
    rga.notes
FROM refineries.refinery_grade_assignments rga
JOIN oil_network.assets       a ON a.asset_id  = rga.refinery_id
JOIN oil_network.commodities  c ON c.commodity = rga.commodity
WHERE a.asset_id LIKE 'ref\_%' ESCAPE '\'
  AND a.kind = 'physical';

COMMENT ON VIEW refineries.v_refinery_grades IS
    'Canonical join: refinery (oil_network.assets) x grade '
    '(oil_network.commodities) x assignment (refineries.refinery_grade_assignments). '
    'Filtered to physical ref_* assets.';

-- Audit view: surfaces slate / NCI / coker mismatches between the assigned
-- grade and the refinery's existing configuration.
--
-- A refinery whose preferred_slate is 'heavy_sour' should not have ONLY
-- sweet grades assigned; a refinery without a coker should not have ONLY
-- heavy sour grades; etc. The thresholds below are heuristics, advisory
-- only — they flag rows that warrant a manual look, not bugs.
CREATE OR REPLACE VIEW refineries.v_refinery_grade_audit AS
WITH per_refinery AS (
    SELECT
        rga.refinery_id,
        a.attributes->'configuration'->>'preferred_slate' AS preferred_slate,
        (a.attributes->'configuration'->>'nelson_complexity_index')::NUMERIC AS nci,
        (a.attributes->'configuration'->>'has_coker')::BOOLEAN AS has_coker,
        COUNT(*) FILTER (WHERE c.density_class IN ('heavy','medium'))   AS n_heavy_med,
        COUNT(*) FILTER (WHERE c.density_class = 'light' OR c.density_class = 'very_light') AS n_light,
        COUNT(*) FILTER (WHERE c.sweet_sour IN ('sour','medium_sour')) AS n_sour,
        COUNT(*) FILTER (WHERE c.sweet_sour = 'sweet')                AS n_sweet,
        COUNT(*) AS n_grades
    FROM refineries.refinery_grade_assignments rga
    JOIN oil_network.assets a       ON a.asset_id  = rga.refinery_id
    JOIN oil_network.commodities c  ON c.commodity = rga.commodity
    GROUP BY rga.refinery_id,
             a.attributes->'configuration'->>'preferred_slate',
             a.attributes->'configuration'->>'nelson_complexity_index',
             a.attributes->'configuration'->>'has_coker'
)
SELECT
    refinery_id,
    preferred_slate,
    nci,
    has_coker,
    n_grades,
    n_heavy_med,
    n_light,
    n_sour,
    n_sweet,
    CASE
        WHEN preferred_slate = 'heavy_sour'  AND n_heavy_med = 0
            THEN 'slate=heavy_sour but no heavy/medium grades assigned'
        WHEN preferred_slate = 'medium_sour' AND n_sour = 0
            THEN 'slate=medium_sour but no sour grades assigned'
        WHEN preferred_slate = 'light_sweet' AND n_sour > n_sweet
            THEN 'slate=light_sweet but majority of grades are sour'
        WHEN has_coker IS NOT TRUE AND nci < 9
              AND n_sour > 0 AND n_sweet = 0
            THEN 'no coker and NCI<9 but only sour grades assigned'
        ELSE NULL
    END AS mismatch_flag
FROM per_refinery;

COMMENT ON VIEW refineries.v_refinery_grade_audit IS
    'Advisory cross-check between assigned grades and the refinery''s existing '
    'preferred_slate / NCI / has_coker. Non-null mismatch_flag rows warrant a '
    'manual look. Does not block the loader.';
"""


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("[1] DDL: schema + table + 2 views")
        cur.execute(DDL)

        conn.commit()

        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM information_schema.schemata
                 WHERE schema_name = 'refineries'),
                (SELECT COUNT(*) FROM information_schema.tables
                 WHERE table_schema = 'refineries'),
                (SELECT COUNT(*) FROM information_schema.views
                 WHERE table_schema = 'refineries'),
                (SELECT COUNT(*) FROM refineries.refinery_grade_assignments)
        """)
        n_schema, n_tables, n_views, n_rows = cur.fetchone()
        print(f"\nFinal state:")
        print(f"  schemas named 'refineries':         {n_schema}  (expect 1)")
        print(f"  tables in refineries.*:             {n_tables}  (expect 1)")
        print(f"  views in refineries.*:              {n_views}  (expect 2)")
        print(f"  refinery_grade_assignments rows:    {n_rows}")


if __name__ == "__main__":
    main()
