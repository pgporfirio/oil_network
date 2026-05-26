"""Create the `production_sites.*` schema for the US_oil_production_sites_explorer agent.

Idempotent — safe to re-run. Adds one schema and nine tables in the `eia_crude`
database. Unlike `refineries.*`, this schema is **stand-alone** — production
fields do not exist in `oil_network.nodes` (that graph is midstream/downstream).
The agent populates the field master row itself during its bootstrap pass.

Tables:

    production_sites.field                — master row, one per producing field
    production_sites.sources              — citation log (URLs the agent visited)
    production_sites.field_operators      — operator + WI-holder history (M:N)
    production_sites.field_grades         — grades produced + assay properties
    production_sites.production_timeseries — sparse step-function rate observations
                                            (mirrors oil_network.timeseries_data:
                                             value at observation_date carries forward
                                             until next observation; bpd unit)
    production_sites.reserves             — reserve estimates (proved, 2P, OOIP, …)
    production_sites.events               — discovery, peak, expansion, ownership change
    production_sites.field_outages        — TAR / planned maintenance / unplanned outages
                                            (hurricanes, curtailments, pipeline disruptions);
                                            time-windowed with optional impact_pct
    production_sites.logistics_links      — pipelines / terminals / rail that move the crude
    production_sites.exploration_runs     — one row per agent invocation (audit log)

Run standalone:

    ..\..\..\.venv\Scripts\python.exe code\migrations\create_production_sites_schema.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
CREATE SCHEMA IF NOT EXISTS production_sites;

CREATE TABLE IF NOT EXISTS production_sites.field (
    field_id              TEXT PRIMARY KEY,             -- slug e.g. 'spraberry_tx'
    name                  TEXT NOT NULL,
    aliases               TEXT[],
    basin                 TEXT,                         -- 'Permian', 'Bakken', 'Eagle Ford', ...
    play                  TEXT,                         -- 'Wolfcamp', 'Spraberry', sub-play
    country               TEXT DEFAULT 'US',
    state                 TEXT,
    onshore_offshore      TEXT,                         -- 'onshore' | 'offshore'
    latitude              NUMERIC(8,5),
    longitude             NUMERIC(9,5),
    discovery_year        INTEGER,
    first_production_year INTEGER,
    status                TEXT,                         -- 'active'|'declining'|'shut-in'|'abandoned'
    field_type            TEXT,                         -- 'conventional'|'tight-oil'|'shale'|'heavy-oil'|'oil-sands'|'offshore-deepwater'
    notes                 TEXT,
    last_explored_at      TIMESTAMPTZ
);

-- Audit log of agent invocations. Defined early so other tables can
-- FK-reference run_id inline. inconsistencies + agent_notes are JSONB
-- and populated by the post-persistence consistency scan and the
-- report_issue MCP tool respectively.
CREATE TABLE IF NOT EXISTS production_sites.exploration_runs (
    run_id              SERIAL PRIMARY KEY,
    field_id            TEXT NOT NULL
                        REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    started_at          TIMESTAMPTZ DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    model               TEXT,
    tool_calls          INTEGER,
    tokens_in           INTEGER,
    tokens_out          INTEGER,
    cost_usd            NUMERIC,
    status              TEXT,         -- 'success' | 'partial' | 'failed' | 'bootstrap'
    error               TEXT,
    summary             TEXT,
    has_inconsistencies BOOLEAN NOT NULL DEFAULT FALSE,
    inconsistencies     JSONB DEFAULT '{}'::jsonb,   -- structured conflict findings from post-persist scan
    agent_notes         JSONB DEFAULT '[]'::jsonb    -- agent self-flagged caveats via report_issue tool
);
CREATE INDEX IF NOT EXISTS exploration_runs_field_idx
    ON production_sites.exploration_runs(field_id);

CREATE TABLE IF NOT EXISTS production_sites.sources (
    source_id     SERIAL PRIMARY KEY,
    field_id      TEXT NOT NULL
                  REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    url           TEXT,
    title         TEXT,
    publisher     TEXT,
    document_type TEXT,         -- 'eia'|'state_regulator'|'10-K'|'press'|'wikipedia'|'trade_press'|'other'
    published_at  DATE,
    fetched_at    TIMESTAMPTZ DEFAULT now(),
    notes         TEXT,
    run_id        INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS sources_field_idx ON production_sites.sources(field_id);
CREATE INDEX IF NOT EXISTS sources_run_idx   ON production_sites.sources(run_id);

CREATE TABLE IF NOT EXISTS production_sites.field_operators (
    field_op_id    SERIAL PRIMARY KEY,
    field_id       TEXT NOT NULL
                   REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    operator_name  TEXT NOT NULL,
    parent_corp    TEXT,
    role           TEXT,        -- 'operator'|'wi_holder'|'royalty'|'former_operator'
    share_pct      NUMERIC(5,2),
    start_year     INTEGER,
    end_year       INTEGER,     -- NULL if current
    source_id      INTEGER REFERENCES production_sites.sources(source_id),
    run_id         INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS field_operators_field_idx ON production_sites.field_operators(field_id);
CREATE INDEX IF NOT EXISTS field_operators_run_idx   ON production_sites.field_operators(run_id);

CREATE TABLE IF NOT EXISTS production_sites.field_grades (
    grade_obs_id   SERIAL PRIMARY KEY,
    field_id       TEXT NOT NULL
                   REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    grade_name     TEXT NOT NULL,                    -- 'WTI Midland', 'Bakken Light', 'ANS', ...
    api_gravity    NUMERIC(5,2),
    sulphur_pct    NUMERIC(5,3),
    share_pct      NUMERIC(5,2),
    period_start   DATE,
    period_end     DATE,
    source_id      INTEGER REFERENCES production_sites.sources(source_id),
    notes          TEXT,
    run_id         INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS field_grades_field_idx ON production_sites.field_grades(field_id);
CREATE INDEX IF NOT EXISTS field_grades_run_idx   ON production_sites.field_grades(run_id);

-- Sparse step-function time-series of field production rates (mirrors
-- oil_network.timeseries_data). A row at observation_date represents the
-- prevailing rate from that date forward, until the next observation
-- overrides it. The agent emits one row per *change point* (annual
-- baseline, outage start, outage end, peak, expansion, restart), not
-- one row per month. Consumer-side resolvers (or the agent during
-- analysis) fill in the implicit constant intervals between rows.
-- Only rate metrics live here (bpd, mcfd, count) — volume metrics
-- (oil_bbl_total) do not have a step-function interpretation and are
-- excluded.
CREATE TABLE IF NOT EXISTS production_sites.production_timeseries (
    field_id          TEXT NOT NULL
                      REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    observation_date  DATE NOT NULL,                  -- any date; value applies from this date forward
    metric            TEXT NOT NULL,                  -- 'oil_bpd'|'gas_mcfd'|'water_bpd'|'wells_count'
    value             NUMERIC,                        -- rate at observation_date; step-function (carries forward)
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

CREATE TABLE IF NOT EXISTS production_sites.reserves (
    reserve_id    SERIAL PRIMARY KEY,
    field_id      TEXT NOT NULL
                  REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    as_of_date    DATE,
    category      TEXT,         -- 'proved'|'probable'|'possible'|'2P'|'3P'|'OOIP'
    commodity     TEXT,         -- 'oil'|'gas'|'ngl'
    volume        NUMERIC,
    unit          TEXT,         -- 'mmbbl'|'bcf'|'mmboe'
    source_id     INTEGER REFERENCES production_sites.sources(source_id),
    notes         TEXT,
    run_id        INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS reserves_field_idx ON production_sites.reserves(field_id);
CREATE INDEX IF NOT EXISTS reserves_run_idx   ON production_sites.reserves(run_id);

CREATE TABLE IF NOT EXISTS production_sites.events (
    event_id       SERIAL PRIMARY KEY,
    field_id       TEXT NOT NULL
                   REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    event_type     TEXT,         -- 'discovery'|'first_production'|'peak'|'expansion'|'acquisition'|'divestiture'|'shutdown'|'fire'|'spill'
    start_date     DATE,
    end_date       DATE,
    description    TEXT,
    source_id      INTEGER REFERENCES production_sites.sources(source_id),
    run_id         INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS events_field_idx ON production_sites.events(field_id);
CREATE INDEX IF NOT EXISTS events_run_idx   ON production_sites.events(run_id);

-- Time-windowed outage ledger. Distinct from `events` (which captures
-- discrete markers like discovery, peak, acquisition). Outages are intervals
-- when production was suppressed and are used as a sanity check on monthly
-- production_monthly values derived from annual/cumulative totals.
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

CREATE TABLE IF NOT EXISTS production_sites.logistics_links (
    link_id       SERIAL PRIMARY KEY,
    field_id      TEXT NOT NULL
                  REFERENCES production_sites.field(field_id) ON DELETE CASCADE,
    asset_type    TEXT,         -- 'pipeline'|'terminal'|'rail_terminal'|'gathering_system'|'refinery'
    asset_name    TEXT,
    operator      TEXT,
    capacity_bpd  INTEGER,
    direction     TEXT,         -- 'outbound'|'inbound' (almost always outbound)
    notes         TEXT,
    source_id     INTEGER REFERENCES production_sites.sources(source_id),
    run_id        INTEGER REFERENCES production_sites.exploration_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS logistics_links_field_idx ON production_sites.logistics_links(field_id);
CREATE INDEX IF NOT EXISTS logistics_links_run_idx   ON production_sites.logistics_links(run_id);
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        print("production_sites schema created (or already existed).")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'production_sites'
                ORDER BY table_name
                """
            )
            tables = [r[0] for r in cur.fetchall()]
        print(f"  tables present: {tables}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
