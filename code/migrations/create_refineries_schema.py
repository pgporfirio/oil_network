"""Create the `refineries.*` schema for the US_refinery_explorer agent outputs.

Idempotent — safe to re-run. Adds one schema and eight tables in the
`eia_crude` database, all FK-tied back to `oil_network.nodes(node_id)`.

Tables (one row per natural key):

    refineries.refinery           — master row per refinery (FK to oil_network.nodes)
    refineries.sources            — citation log (URLs the agent visited)
    refineries.process_units      — per-refinery unit inventory (FCC, coker, …)
    refineries.slate              — crude slate composition observations
    refineries.events             — turnarounds, fires, expansions, ownership changes
    refineries.financials         — segment-level 10-K/10-Q extracts
    refineries.runs_monthly       — synthesised monthly throughput series
    refineries.exploration_runs   — one row per agent invocation (audit log)

Run standalone:

    ..\..\..\.venv\Scripts\python.exe code\migrations\create_refineries_schema.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
CREATE SCHEMA IF NOT EXISTS refineries;

CREATE TABLE IF NOT EXISTS refineries.refinery (
    refinery_id      TEXT PRIMARY KEY
                     REFERENCES oil_network.nodes(node_id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    corporation      TEXT,
    operator         TEXT,
    site             TEXT,
    state            TEXT,
    padd             TEXT,
    rdist_label      TEXT,
    capacity_bpd     INTEGER,
    duoarea_code     TEXT,
    last_explored_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS refineries.sources (
    source_id     SERIAL PRIMARY KEY,
    refinery_id   TEXT NOT NULL
                  REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    url           TEXT,
    title         TEXT,
    publisher     TEXT,
    document_type TEXT,
    published_at  DATE,
    fetched_at    TIMESTAMPTZ DEFAULT now(),
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS sources_refinery_idx
    ON refineries.sources(refinery_id);

CREATE TABLE IF NOT EXISTS refineries.process_units (
    refinery_id  TEXT NOT NULL
                 REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    unit_type    TEXT NOT NULL,
    capacity_bpd INTEGER,
    status       TEXT,
    source_id    INTEGER REFERENCES refineries.sources(source_id),
    PRIMARY KEY (refinery_id, unit_type)
);

CREATE TABLE IF NOT EXISTS refineries.slate (
    refinery_id  TEXT NOT NULL
                 REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    period_start DATE NOT NULL,
    period_end   DATE,
    grade_name   TEXT NOT NULL,
    api_gravity  NUMERIC(5,2),
    sulphur_pct  NUMERIC(5,3),
    share_pct    NUMERIC(5,2),
    source_id    INTEGER REFERENCES refineries.sources(source_id),
    PRIMARY KEY (refinery_id, period_start, grade_name)
);

CREATE TABLE IF NOT EXISTS refineries.events (
    event_id            SERIAL PRIMARY KEY,
    refinery_id         TEXT NOT NULL
                        REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    event_type          TEXT NOT NULL,
    start_date          DATE,
    end_date            DATE,
    units_affected      TEXT,
    capacity_impact_bpd INTEGER,
    description         TEXT,
    source_id           INTEGER REFERENCES refineries.sources(source_id)
);
CREATE INDEX IF NOT EXISTS events_refinery_idx
    ON refineries.events(refinery_id);

CREATE TABLE IF NOT EXISTS refineries.financials (
    refinery_id     TEXT NOT NULL
                    REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    period_start    DATE NOT NULL,
    period_end      DATE,
    period_type     TEXT NOT NULL,           -- 'Q1' | 'Q2' | 'Q3' | 'Q4' | 'FY'
    throughput_bpd  INTEGER,
    utilisation_pct NUMERIC(5,2),
    revenue_usd_m   NUMERIC,
    ebitda_usd_m    NUMERIC,
    source_id       INTEGER REFERENCES refineries.sources(source_id),
    PRIMARY KEY (refinery_id, period_start, period_type)
);

CREATE TABLE IF NOT EXISTS refineries.runs_monthly (
    refinery_id  TEXT NOT NULL
                 REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    month        DATE NOT NULL,              -- always day 01
    metric       TEXT NOT NULL,              -- 'crude_runs_bpd' | 'utilisation_pct' | 'capacity_bpd'
    value        NUMERIC,
    method       TEXT,                       -- 'eia_attributed' | 'financials_quarterly_split' | …
    confidence   TEXT,                       -- 'high' | 'medium' | 'low'
    source_id    INTEGER REFERENCES refineries.sources(source_id),
    notes        TEXT,
    PRIMARY KEY (refinery_id, month, metric)
);
CREATE INDEX IF NOT EXISTS runs_monthly_metric_idx
    ON refineries.runs_monthly(metric, month);

CREATE TABLE IF NOT EXISTS refineries.exploration_runs (
    run_id       SERIAL PRIMARY KEY,
    refinery_id  TEXT NOT NULL
                 REFERENCES refineries.refinery(refinery_id) ON DELETE CASCADE,
    started_at   TIMESTAMPTZ DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    model        TEXT,
    tool_calls   INTEGER,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost_usd     NUMERIC,
    status       TEXT,                       -- 'success' | 'partial' | 'failed'
    error        TEXT,
    summary      TEXT
);
CREATE INDEX IF NOT EXISTS exploration_runs_refinery_idx
    ON refineries.exploration_runs(refinery_id);
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        print("refineries schema created (or already existed).")

        # Quick visibility check.
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
        print(f"  tables present: {tables}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
