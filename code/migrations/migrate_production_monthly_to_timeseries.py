"""Second-pass production-history migration: replace
``production_sites.production_monthly`` with
``production_sites.production_timeseries``.

Rationale: the monthly-grain table I built first stores one row per
(field, month, metric) with the rate value replicated across every month
in the period. That doesn't match how ``oil_network.timeseries_data``
represents production — there, observations are sparse with a step-
function interpretation: a value at observation_date carries forward
until the next observation overrides it.

This migration aligns production_sites with the canonical
``oil_network.timeseries_data`` shape:

- Column rename ``month`` → ``observation_date`` (DATE, any date, not
  constrained to month starts).
- Same PK ``(field_id, observation_date, metric)``.
- Step-function semantic — consumers carry the latest value forward.
- Sparse rows — the agent emits one observation per *change point*, not
  one per month. Outages become a zero-value row at the outage start
  and a restore-value row at the outage end.
- Values are rates (bpd) — volume metrics (oil_bbl_total) are removed
  from the agent's allowed metrics, since they break the step-function
  semantic (a cumulative number doesn't carry forward).

This drops all 896 monthly rows currently in production_monthly. The 3
calibration fields need to be re-explored under the new shape (cost
~\$2 each).

Idempotent — safe to re-run.

Run from ``Stage2/``::

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\migrate_production_monthly_to_timeseries.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
-- 1. Drop the dense monthly table. CASCADE removes dependent views
--    (v_field_overview, v_field_production, v_field_production_summary).
--    Re-run create_production_sites_views.py afterwards to rebuild them
--    over the new production_timeseries table.
DROP TABLE IF EXISTS production_sites.production_monthly CASCADE;

-- 2. Create the sparse step-function table — mirrors oil_network.timeseries_data
--    in shape (field_id replaces timeseries_id; metric is the secondary key
--    because one field can carry multiple series — oil_bpd, gas_mcfd, etc.).
CREATE TABLE IF NOT EXISTS production_sites.production_timeseries (
    field_id          TEXT NOT NULL
                      REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    observation_date  DATE NOT NULL,                  -- any date; value applies from this date forward
    metric            TEXT NOT NULL,                  -- 'oil_bpd'|'gas_mcfd'|'water_bpd'|'wells_count' (rate metrics only)
    value             NUMERIC,                        -- value at observation_date; carries forward (step function)
    method            TEXT,                           -- 'state_reg'|'eia_attributed'|'operator_10K'|'outage_event'|'restore'|'estimated'
    confidence        TEXT,                           -- 'high'|'medium'|'low'
    source_id         INTEGER REFERENCES production_sites.sources(source_id),
    notes             TEXT,
    run_id            INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL,
    PRIMARY KEY (field_id, observation_date, metric)
);
CREATE INDEX IF NOT EXISTS production_timeseries_metric_idx
    ON production_sites.production_timeseries(metric, observation_date);
CREATE INDEX IF NOT EXISTS production_timeseries_field_idx
    ON production_sites.production_timeseries(field_id);
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
        print("migration applied.")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'production_sites'
                  AND table_name = 'production_timeseries'
                ORDER BY ordinal_position
                """
            )
            cols = cur.fetchall()
        print("production_sites.production_timeseries columns:")
        for c, t in cols:
            print(f"  {c:20} {t}")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='production_sites' "
                "AND table_name='production_monthly')"
            )
            still_there = cur.fetchone()[0]
        print(f"old production_monthly table still exists: {still_there}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
