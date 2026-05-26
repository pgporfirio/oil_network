"""Add `refineries.monthly_slate_distribution` — per-month probability
distribution over commodities for each refinery's synthesised time-series.

Idempotent. Adds:

  - `refineries.monthly_slate_distribution` — one row per
    `(refinery_id, month, commodity)`. `probability` ∈ [0, 1]. The set of rows
    for a given `(refinery_id, month)` should sum to ~1.0 (validated via the
    QA view below, not enforced as a constraint).
  - `refineries.v_slate_dist_unbalanced` — diagnostic view listing
    `(refinery_id, month, sum_probability, n_commodities)` for months where
    `|sum - 1.0| > 0.005`. A healthy refinery has zero rows here.

Why two roles for slate data:

  refineries.slate                    period-level observations (input).
                                      e.g. "1997-2007: PA Grade 60%, Ohio 40%".
                                      May be overlapping or contradictory.
  refineries.monthly_slate_distribution  per-month resolved distribution (output).
                                         derived from slate periods (baseline) and
                                         overridable by the agent for specific months.

Run standalone:

    ..\\..\\..\\.venv\\Scripts\\python.exe code\\migrations\\add_monthly_slate_distribution.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
CREATE TABLE IF NOT EXISTS refineries.monthly_slate_distribution (
    refinery_id  TEXT NOT NULL
                 REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    month        DATE NOT NULL,                       -- always day 01
    commodity    TEXT NOT NULL
                 REFERENCES refineries.commodities(commodity),
    probability  NUMERIC(6,4) NOT NULL
                 CHECK (probability >= 0 AND probability <= 1),
    method       TEXT,            -- 'derived_from_slate_period' | 'agent_override' | 'fallback_primary'
    confidence   TEXT,            -- 'high' | 'medium' | 'low'
    source_id    INTEGER REFERENCES refineries.sources(source_id),
    run_id       INTEGER REFERENCES refineries.exploration_runs(run_id) ON DELETE SET NULL,
    notes        TEXT,
    PRIMARY KEY (refinery_id, month, commodity)
);
CREATE INDEX IF NOT EXISTS msd_month_commodity_idx
    ON refineries.monthly_slate_distribution(month, commodity);
CREATE INDEX IF NOT EXISTS msd_refinery_month_idx
    ON refineries.monthly_slate_distribution(refinery_id, month);

CREATE OR REPLACE VIEW refineries.v_slate_dist_unbalanced AS
SELECT refinery_id,
       month,
       ROUND(SUM(probability)::numeric, 4) AS sum_probability,
       COUNT(*)                             AS n_commodities
FROM   refineries.monthly_slate_distribution
GROUP BY refinery_id, month
HAVING ABS(SUM(probability) - 1.0) > 0.005
ORDER BY refinery_id, month;
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        print("monthly_slate_distribution table + v_slate_dist_unbalanced view applied.")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM refineries.monthly_slate_distribution;"
            )
            n_rows = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM refineries.v_slate_dist_unbalanced;")
            n_unbalanced = cur.fetchone()[0]
        print(f"  rows: {n_rows}")
        print(f"  unbalanced refinery-months: {n_unbalanced}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
