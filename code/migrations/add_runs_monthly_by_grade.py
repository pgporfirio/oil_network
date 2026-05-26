"""Add `refineries.runs_monthly_by_grade` — per-grade decomposition of each
runs_monthly row.

Idempotent. Adds:

  - `refineries.runs_monthly_by_grade` — one row per
    `(refinery_id, month, metric, commodity)`. `value` is the per-commodity
    bpd. Composite FK to `runs_monthly` ensures every breakdown row maps to
    an existing aggregate row. The set of `value`s for a given
    `(refinery_id, month, metric)` should sum to the aggregate
    `runs_monthly.value` (validated via the QA view below).
  - `refineries.v_runs_monthly_by_grade_unbalanced` — diagnostic view listing
    `(refinery_id, month, metric, aggregate, sum_per_grade, delta)` for rows
    where the sum disagrees with the aggregate by more than 0.5%. Healthy
    refineries have zero rows here.

Why a separate table (rather than splitting runs_monthly):

  - Aggregate queries (total monthly throughput) continue to hit runs_monthly
    with no JOIN.
  - Per-grade rows can be **derived** from runs_monthly ×
    monthly_slate_distribution, or **directly observed** from sources that
    disclose per-grade volumes (sustainability reports, EIA Crude Oil Input
    by Source). Source tracking is per row.

Run standalone:

    ..\\..\\..\\.venv\\Scripts\\python.exe code\\migrations\\add_runs_monthly_by_grade.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
CREATE TABLE IF NOT EXISTS refineries.runs_monthly_by_grade (
    refinery_id  TEXT NOT NULL,
    month        DATE NOT NULL,                   -- always day 01
    metric       TEXT NOT NULL,                   -- mirrors runs_monthly.metric
    commodity    TEXT NOT NULL
                 REFERENCES refineries.commodities(commodity),
    value        NUMERIC,                          -- per-commodity bpd
    source       TEXT,                             -- 'derived_from_slate_dist' | 'observed' | etc.
    confidence   TEXT,                             -- 'high' | 'medium' | 'low'
    source_id    INTEGER REFERENCES refineries.sources(source_id),
    run_id       INTEGER REFERENCES refineries.exploration_runs(run_id) ON DELETE SET NULL,
    notes        TEXT,
    PRIMARY KEY (refinery_id, month, metric, commodity),
    FOREIGN KEY (refinery_id, month, metric)
        REFERENCES refineries.runs_monthly(refinery_id, month, metric)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS rmbg_month_commodity_idx
    ON refineries.runs_monthly_by_grade(month, commodity);
CREATE INDEX IF NOT EXISTS rmbg_refinery_month_idx
    ON refineries.runs_monthly_by_grade(refinery_id, month);

CREATE OR REPLACE VIEW refineries.v_runs_monthly_by_grade_unbalanced AS
SELECT  rm.refinery_id,
        rm.month,
        rm.metric,
        rm.value                            AS aggregate,
        ROUND(COALESCE(SUM(g.value), 0)::numeric, 2) AS sum_per_grade,
        ROUND((rm.value - COALESCE(SUM(g.value), 0))::numeric, 2) AS delta
FROM    refineries.runs_monthly rm
LEFT JOIN refineries.runs_monthly_by_grade g
       ON g.refinery_id = rm.refinery_id
      AND g.month       = rm.month
      AND g.metric      = rm.metric
GROUP BY rm.refinery_id, rm.month, rm.metric, rm.value
HAVING  ABS(rm.value - COALESCE(SUM(g.value), 0)) > GREATEST(0.005 * ABS(rm.value), 0.5)
ORDER BY rm.refinery_id, rm.month, rm.metric;
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        print("runs_monthly_by_grade table + v_runs_monthly_by_grade_unbalanced view applied.")

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM refineries.runs_monthly_by_grade;")
            n_rows = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM refineries.v_runs_monthly_by_grade_unbalanced;")
            n_unbal = cur.fetchone()[0]
        print(f"  rows: {n_rows}")
        print(f"  unbalanced refinery-month-metric triples: {n_unbal}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
