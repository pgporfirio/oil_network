"""Migrate refinery consumption tables from monthly-bucket semantics to
proper time-series semantics (observation_date + LOCF), aligning with the
main schema's `oil_network.timeseries_data` convention.

What this does (idempotent):

  - drops the two QA views that reference the old column names
  - renames `runs_monthly`              → `runs_ts`
  - renames `monthly_slate_distribution` → `slate_distribution_ts`
  - renames `runs_monthly_by_grade`     → `runs_by_grade_ts`
  - renames every `month` column        → `observation_date`
  - rebuilds the QA views under the new names
  - renames indexes for consistency

Semantic shift (no data is rewritten):

  Before: a row with `month='2024-01-01'` meant "this is January 2024's
          monthly-bucket value".
  After:  a row with `observation_date='2024-01-01'` means "starting on
          2024-01-01, the value is held until the next observation"
          (last-observation-carried-forward).

The existing first-of-month rows remain valid under TS semantics — they
just now read as "step function with monthly observations" instead of
"monthly averages". Future agent emissions can land on any date the
underlying evidence supports (e.g. 2024-05-15 for an investor-day
disclosure), not just the first of each month.

Run standalone:

    ..\\..\\..\\.venv\\Scripts\\python.exe code\\migrations\\move_to_ts_semantics.py
"""
from __future__ import annotations

import psycopg2


DDL_RENAME = r"""
-- Drop QA views first; they reference the old column names.
DROP VIEW IF EXISTS refineries.v_slate_dist_unbalanced;
DROP VIEW IF EXISTS refineries.v_runs_monthly_by_grade_unbalanced;

-- Rename columns. The composite FK from runs_monthly_by_grade →
-- runs_monthly auto-updates via PostgreSQL's catalog rewrite, but we
-- have to rename each side's `month` column.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='refineries' AND table_name='runs_monthly'
          AND column_name='month'
    ) THEN
        EXECUTE 'ALTER TABLE refineries.runs_monthly
                 RENAME COLUMN month TO observation_date';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='refineries' AND table_name='monthly_slate_distribution'
          AND column_name='month'
    ) THEN
        EXECUTE 'ALTER TABLE refineries.monthly_slate_distribution
                 RENAME COLUMN month TO observation_date';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='refineries' AND table_name='runs_monthly_by_grade'
          AND column_name='month'
    ) THEN
        EXECUTE 'ALTER TABLE refineries.runs_monthly_by_grade
                 RENAME COLUMN month TO observation_date';
    END IF;
END $$;

-- Rename tables. FK references update automatically.
ALTER TABLE IF EXISTS refineries.runs_monthly                  RENAME TO runs_ts;
ALTER TABLE IF EXISTS refineries.monthly_slate_distribution    RENAME TO slate_distribution_ts;
ALTER TABLE IF EXISTS refineries.runs_monthly_by_grade         RENAME TO runs_by_grade_ts;

-- Recreate QA views under the new names + columns.
CREATE OR REPLACE VIEW refineries.v_slate_dist_unbalanced AS
SELECT refinery_id,
       observation_date,
       ROUND(SUM(probability)::numeric, 4) AS sum_probability,
       COUNT(*)                             AS n_commodities
FROM   refineries.slate_distribution_ts
GROUP BY refinery_id, observation_date
HAVING ABS(SUM(probability) - 1.0) > 0.005
ORDER BY refinery_id, observation_date;

CREATE OR REPLACE VIEW refineries.v_runs_by_grade_unbalanced AS
SELECT  rm.refinery_id,
        rm.observation_date,
        rm.metric,
        rm.value                                                       AS aggregate,
        ROUND(COALESCE(SUM(g.value), 0)::numeric, 2)                   AS sum_per_grade,
        ROUND((rm.value - COALESCE(SUM(g.value), 0))::numeric, 2)      AS delta
FROM    refineries.runs_ts rm
LEFT JOIN refineries.runs_by_grade_ts g
       ON g.refinery_id      = rm.refinery_id
      AND g.observation_date = rm.observation_date
      AND g.metric           = rm.metric
GROUP BY rm.refinery_id, rm.observation_date, rm.metric, rm.value
HAVING  ABS(rm.value - COALESCE(SUM(g.value), 0)) > GREATEST(0.005 * ABS(rm.value), 0.5)
ORDER BY rm.refinery_id, rm.observation_date, rm.metric;

-- Rename indexes for consistency (best-effort; ignore if absent).
ALTER INDEX IF EXISTS refineries.runs_monthly_metric_idx              RENAME TO runs_ts_metric_idx;
ALTER INDEX IF EXISTS refineries.msd_month_commodity_idx              RENAME TO sdts_obs_commodity_idx;
ALTER INDEX IF EXISTS refineries.msd_refinery_month_idx               RENAME TO sdts_refinery_obs_idx;
ALTER INDEX IF EXISTS refineries.rmbg_month_commodity_idx             RENAME TO rbgts_obs_commodity_idx;
ALTER INDEX IF EXISTS refineries.rmbg_refinery_month_idx              RENAME TO rbgts_refinery_obs_idx;
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL_RENAME)
        print("migrated refinery consumption tables to TS semantics.")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'refineries'
                ORDER BY table_name
                """
            )
            tables = [r[0] for r in cur.fetchall()]
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='refineries' AND table_name='runs_ts'
                ORDER BY ordinal_position
                """
            )
            cols = [r[0] for r in cur.fetchall()]
        print(f"  tables: {tables}")
        print(f"  runs_ts columns: {cols}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
