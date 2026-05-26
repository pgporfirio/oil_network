"""Add ``run_id`` traceability to all child tables + inconsistency / agent-
notes columns to ``exploration_runs``.

Why: the agent is non-deterministic, so re-runs produce slightly different
data. Without ``run_id`` on the child tables, re-runs accumulate duplicate
rows that can't be attributed to a producing run. With it, every row is
traceable and a post-persist consistency scan can detect "same logical
entity emitted under multiple runs" and write the findings to
``exploration_runs.inconsistencies`` (JSONB).

Tables receiving ``run_id INTEGER REFERENCES exploration_runs(run_id) ON DELETE SET NULL``:

    sources
    field_operators
    field_grades
    reserves
    events
    logistics_links

(``production_timeseries`` and ``field_outages`` already have ``run_id``.)

New columns on ``exploration_runs``:

    has_inconsistencies BOOLEAN NOT NULL DEFAULT FALSE
    inconsistencies     JSONB             DEFAULT '{}'::jsonb   -- structured conflict findings
    agent_notes         JSONB             DEFAULT '[]'::jsonb   -- agent self-flagged caveats

Idempotent — safe to re-run. Existing rows get ``run_id = NULL`` (untracked
legacy) and the inconsistency columns get their defaults.

Run from ``Stage2/``::

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\add_run_id_and_inconsistency_tracking.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
-- 1. Add run_id columns to the six child tables that don't have them.
ALTER TABLE production_sites.sources
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS sources_run_idx ON production_sites.sources(run_id);

ALTER TABLE production_sites.field_operators
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS field_operators_run_idx ON production_sites.field_operators(run_id);

ALTER TABLE production_sites.field_grades
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS field_grades_run_idx ON production_sites.field_grades(run_id);

ALTER TABLE production_sites.reserves
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS reserves_run_idx ON production_sites.reserves(run_id);

ALTER TABLE production_sites.events
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS events_run_idx ON production_sites.events(run_id);

ALTER TABLE production_sites.logistics_links
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS logistics_links_run_idx ON production_sites.logistics_links(run_id);

-- 2. Add inconsistency-tracking columns to exploration_runs.
ALTER TABLE production_sites.exploration_runs
    ADD COLUMN IF NOT EXISTS has_inconsistencies BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS inconsistencies     JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS agent_notes         JSONB DEFAULT '[]'::jsonb;

-- 3. View to flatten the inconsistencies JSON for human review.
CREATE OR REPLACE VIEW production_sites.v_run_inconsistencies AS
SELECT
    er.run_id,
    er.field_id,
    er.started_at,
    er.model,
    er.status,
    table_name,
    issue->>'natural_key'                      AS natural_key,
    issue->'run_ids'                           AS run_ids_involved,
    jsonb_array_length(issue->'rows')          AS n_conflicting_rows,
    issue->'rows'                              AS conflicting_rows
FROM production_sites.exploration_runs er
CROSS JOIN LATERAL jsonb_each(er.inconsistencies) AS t(table_name, issues)
CROSS JOIN LATERAL jsonb_array_elements(t.issues) AS issue
WHERE er.has_inconsistencies
ORDER BY er.run_id DESC, table_name, natural_key;

-- 4. View to flatten the agent_notes JSON for human review.
CREATE OR REPLACE VIEW production_sites.v_run_agent_notes AS
SELECT
    er.run_id,
    er.field_id,
    er.started_at,
    er.model,
    er.status,
    note->>'type'        AS issue_type,
    note->>'severity'    AS severity,
    note->>'description' AS description
FROM production_sites.exploration_runs er
CROSS JOIN LATERAL jsonb_array_elements(er.agent_notes) AS note
WHERE jsonb_array_length(er.agent_notes) > 0
ORDER BY er.run_id DESC;
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
        print("run_id + inconsistency-tracking migration applied.")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name,
                    bool_or(column_name = 'run_id') AS has_run_id
                FROM information_schema.columns
                WHERE table_schema = 'production_sites'
                  AND table_name IN (
                      'sources', 'field_operators', 'field_grades', 'reserves',
                      'events', 'field_outages', 'logistics_links',
                      'production_timeseries', 'exploration_runs'
                  )
                GROUP BY table_name
                ORDER BY table_name
                """
            )
            print("run_id presence per table:")
            for tn, has in cur.fetchall():
                print(f"  {tn:25} run_id={'YES' if has else 'no '}")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, column_default
                FROM information_schema.columns
                WHERE table_schema='production_sites'
                  AND table_name='exploration_runs'
                  AND column_name IN ('has_inconsistencies', 'inconsistencies', 'agent_notes')
                ORDER BY column_name
                """
            )
            print("\nNew columns on exploration_runs:")
            for c, t, d in cur.fetchall():
                print(f"  {c:25} {t:10} default={d}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
