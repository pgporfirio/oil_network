"""Extend the `refineries` schema with a per-(refinery, grade) yield slate.

Adds (idempotent):

  - TABLE  refineries.refinery_grade_slate
       (refinery_id, commodity, product_code, yield_pct, source, notes)
       FKs to oil_network.assets, oil_network.commodities and
       products.oil_products. PK = (refinery_id, commodity, product_code).
       Composite FK to refineries.refinery_grade_assignments
       guarantees a slate row only exists for an assigned (refinery, grade)
       pair (cannot drift out of sync).

  - VIEW   refineries.v_refinery_grade_slate
       Canonical join: assignment row + product metadata + refinery
       configuration + grade properties. One row per
       (refinery, grade, product). For downstream LP / reporting use.

  - VIEW   refineries.v_refinery_slate_audit
       Per-(refinery, grade) sum check. Flags any slate whose yields sum
       outside the [95, 105] tolerance band (allowing for processing gain
       and refinery fuel/own-use loss). Advisory.

The yield_pct column is **volume %** (bbl product per 100 bbl crude). Sums
to ~100% per (refinery, grade); processing gain (~+2-4%) or refinery fuel
loss (~-2%) is allowed inside the audit tolerance band.

Run via:

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\build_refinery_slate_schema.py
"""
from __future__ import annotations

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


DDL = r"""
CREATE TABLE IF NOT EXISTS refineries.refinery_grade_slate (
    refinery_id   TEXT NOT NULL,
    commodity     TEXT NOT NULL,
    product_code  TEXT NOT NULL
                  REFERENCES products.oil_products(product_code) ON DELETE RESTRICT,
    yield_pct     NUMERIC NOT NULL
                  CHECK (yield_pct >= 0 AND yield_pct <= 100),
    source        TEXT,
    notes         TEXT,
    PRIMARY KEY (refinery_id, commodity, product_code),
    -- Composite FK: a slate row can only exist for a (refinery, grade) pair
    -- that has been assigned in refinery_grade_assignments. This prevents
    -- slates from drifting out of sync with the grade-capability table.
    FOREIGN KEY (refinery_id, commodity)
        REFERENCES refineries.refinery_grade_assignments(refinery_id, commodity)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rgs_refinery
    ON refineries.refinery_grade_slate (refinery_id);
CREATE INDEX IF NOT EXISTS idx_rgs_commodity
    ON refineries.refinery_grade_slate (commodity);
CREATE INDEX IF NOT EXISTS idx_rgs_product
    ON refineries.refinery_grade_slate (product_code);

COMMENT ON TABLE refineries.refinery_grade_slate IS
    'Per-(refinery, grade) crude-to-products yield slate. yield_pct is '
    'volume percent (bbl product per 100 bbl crude); rows for one '
    '(refinery, commodity) pair sum to ~100% (see v_refinery_slate_audit).';
COMMENT ON COLUMN refineries.refinery_grade_slate.source IS
    'Provenance label: algorithmic_baseline / company_disclosure / '
    'industry_report / web_research / refined_by_web_override.';

-- Canonical join: slate row + product metadata + refinery context + grade context.
CREATE OR REPLACE VIEW refineries.v_refinery_grade_slate AS
SELECT
    s.refinery_id,
    a.name                                       AS refinery_name,
    a.attributes->'configuration'->>'site'       AS site,
    a.attributes->'configuration'->>'operator'   AS operator,
    (a.attributes->'configuration'->>'capacity_bd')::NUMERIC AS capacity_bd,
    (a.attributes->'configuration'->>'nelson_complexity_index')::NUMERIC AS nci,
    a.attributes->'configuration'->>'preferred_slate' AS preferred_slate,
    (a.attributes->'configuration'->>'has_coker')::BOOLEAN AS has_coker,
    (a.attributes->'configuration'->>'has_hydrocracker')::BOOLEAN AS has_hydrocracker,
    l.padd,
    l.state,
    s.commodity,
    c.sweet_sour,
    c.density_class,
    c.api_gravity_min,
    c.api_gravity_max,
    s.product_code,
    p.name                                       AS product_name,
    p.category                                   AS product_category,
    p.parent_code                                AS product_parent_code,
    s.yield_pct,
    s.source,
    s.notes
FROM refineries.refinery_grade_slate s
JOIN oil_network.assets       a ON a.asset_id  = s.refinery_id
LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
JOIN oil_network.commodities  c ON c.commodity = s.commodity
JOIN products.oil_products    p ON p.product_code = s.product_code
WHERE a.asset_id LIKE 'ref\_%' ESCAPE '\'
  AND a.kind = 'physical';

COMMENT ON VIEW refineries.v_refinery_grade_slate IS
    'Canonical join: refinery (oil_network.assets) x grade '
    '(oil_network.commodities) x slate (refineries.refinery_grade_slate) x '
    'product (products.oil_products). One row per (refinery, grade, product).';

-- Audit view: sum of yields per (refinery, grade) should be ~100%.
-- Flags any slate whose sum is outside the [95, 105] tolerance band.
CREATE OR REPLACE VIEW refineries.v_refinery_slate_audit AS
SELECT
    refinery_id,
    commodity,
    COUNT(*)              AS n_products,
    SUM(yield_pct)        AS total_yield_pct,
    MIN(yield_pct)        AS min_yield_pct,
    MAX(yield_pct)        AS max_yield_pct,
    CASE
        WHEN SUM(yield_pct) < 95
            THEN 'sum < 95% (under-allocated)'
        WHEN SUM(yield_pct) > 105
            THEN 'sum > 105% (over-allocated)'
        ELSE NULL
    END                   AS sum_flag,
    CASE
        WHEN COUNT(*) FILTER (WHERE source = 'company_disclosure') > 0
            THEN 'company_disclosure'
        WHEN COUNT(*) FILTER (WHERE source IN ('industry_report','refined_by_web_override','web_research')) > 0
            THEN 'web_research'
        WHEN COUNT(*) FILTER (WHERE source = 'algorithmic_baseline') > 0
            THEN 'algorithmic_baseline'
        ELSE 'unknown'
    END                   AS dominant_source
FROM refineries.refinery_grade_slate
GROUP BY refinery_id, commodity;

COMMENT ON VIEW refineries.v_refinery_slate_audit IS
    'Per-(refinery, grade) slate sum check. Rows where sum_flag IS NOT '
    'NULL warrant a manual look; processing gain/loss should leave the '
    'total inside [95, 105].';
"""


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("[1] DDL: table + 2 views")
        cur.execute(DDL)
        conn.commit()

        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM information_schema.tables
                 WHERE table_schema='refineries' AND table_name='refinery_grade_slate'),
                (SELECT COUNT(*) FROM information_schema.views
                 WHERE table_schema='refineries' AND table_name IN ('v_refinery_grade_slate','v_refinery_slate_audit')),
                (SELECT COUNT(*) FROM refineries.refinery_grade_slate)
        """)
        n_tab, n_view, n_rows = cur.fetchone()
        print(f"\nFinal state:")
        print(f"  refineries.refinery_grade_slate exists:    {n_tab}  (expect 1)")
        print(f"  slate views created:                       {n_view}  (expect 2)")
        print(f"  refinery_grade_slate rows:                 {n_rows}")


if __name__ == "__main__":
    main()
