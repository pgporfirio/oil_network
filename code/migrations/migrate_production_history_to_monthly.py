"""One-shot migration: replace ``production_sites.production_history``
(range-period shape: ``period_start`` + ``period_type``) with
``production_sites.production_monthly`` (monthly time series, mirroring
``refineries.runs_monthly``).

Rationale: the production-sites schema originally allowed any period
(annual/monthly mix) in one table, which made it awkward to slice month-on-
month. We're moving to the same monthly-grain pattern the refinery agent uses
so the two workstreams stack cleanly downstream. Cumulative or annual
observations are exploded into per-month rows at persistence time, not
stored as a range.

Idempotent: safe to re-run. Drops the old table only if it exists, creates
the new one only if it doesn't.

Run from ``Stage2/``::

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\migrate_production_history_to_monthly.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
-- 1. Drop the old range-period table.
DROP TABLE IF EXISTS production_sites.production_history;

-- 2. Create the monthly time-series table.
CREATE TABLE IF NOT EXISTS production_sites.production_monthly (
    field_id    TEXT NOT NULL
                REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    month       DATE NOT NULL,                       -- always YYYY-MM-01
    metric      TEXT NOT NULL,                       -- 'oil_bpd'|'oil_bbl_total'|'gas_mcfd'|'water_bpd'|'wells_count'
    value       NUMERIC,
    method      TEXT,                                -- 'state_reg'|'eia_attributed'|'operator_10K'|'annual_split'|'cumulative_split'|'estimated'
    confidence  TEXT,                                -- 'high'|'medium'|'low'
    source_id   INTEGER REFERENCES production_sites.sources(source_id),
    notes       TEXT,
    run_id      INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL,
    PRIMARY KEY (field_id, month, metric)
);

CREATE INDEX IF NOT EXISTS production_monthly_metric_idx
    ON production_sites.production_monthly(metric, month);
CREATE INDEX IF NOT EXISTS production_monthly_field_idx
    ON production_sites.production_monthly(field_id);
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
                  AND table_name = 'production_monthly'
                ORDER BY ordinal_position
                """
            )
            cols = cur.fetchall()
        print("production_sites.production_monthly columns:")
        for c, t in cols:
            print(f"  {c:12} {t}")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='production_sites' "
                "AND table_name='production_history')"
            )
            still_there = cur.fetchone()[0]
        print(f"old production_history table still exists: {still_there}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
