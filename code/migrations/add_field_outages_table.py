"""Add ``production_sites.field_outages`` — historical outage / turnaround
ledger for each producing field.

Rationale: when the explorer agent only has annual production data and splits
it evenly across 12 months, the result hides any month-scale outages
(turnarounds, hurricanes in the GoM, pipeline disruptions, OPEC-style
curtailments, fires, freeze-offs). Recording outages alongside the monthly
series lets us (a) sanity-check the monthly numbers, (b) optionally re-bucket
the annual total across only the operating months instead of all 12.

This complements the ``events`` table (which captures discrete events like
discovery, acquisition, peak) rather than replacing it. Outages are
time-windowed and may carry a partial-impact percentage; events are
point-in-time markers.

Idempotent — safe to re-run.

Run from ``Stage2/``::

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\add_field_outages_table.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
CREATE TABLE IF NOT EXISTS production_sites.field_outages (
    outage_id     SERIAL PRIMARY KEY,
    field_id      TEXT NOT NULL
                  REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    start_date    DATE NOT NULL,
    end_date      DATE,                         -- NULL if ongoing or unknown
    outage_type   TEXT,                         -- 'TAR'|'unplanned_maintenance'|'hurricane'|'storm'|'curtailment'|'pipeline'|'fire'|'spill'|'freeze'|'power'|'shutdown'|'other'
    impact_pct    NUMERIC(5,2),                 -- production reduction % 0-100; NULL = full shutdown or unknown
    description   TEXT,
    source_id     INTEGER REFERENCES production_sites.sources(source_id),
    run_id        INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS field_outages_field_idx
    ON production_sites.field_outages(field_id);
CREATE INDEX IF NOT EXISTS field_outages_start_idx
    ON production_sites.field_outages(start_date);

-- Convenience view: monthly production with overlapping outage flag.
-- A month is flagged if any outage covers any day of the month.
CREATE OR REPLACE VIEW production_sites.v_field_outages AS
SELECT
    fo.outage_id,
    fo.field_id,
    f.name           AS field_name,
    fo.start_date,
    fo.end_date,
    fo.outage_type,
    fo.impact_pct,
    fo.description,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM production_sites.field_outages fo
JOIN production_sites.field f USING (field_id)
LEFT JOIN production_sites.sources s ON s.source_id = fo.source_id
ORDER BY fo.field_id, fo.start_date DESC NULLS LAST, fo.outage_id;
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
        print("field_outages table + v_field_outages view created (or already existed).")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'production_sites'
                  AND table_name = 'field_outages'
                ORDER BY ordinal_position
                """
            )
            cols = cur.fetchall()
        print("production_sites.field_outages columns:")
        for c, t in cols:
            print(f"  {c:15} {t}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
