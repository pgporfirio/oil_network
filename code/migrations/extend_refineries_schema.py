"""Extend the `refineries.*` schema (second migration).

Idempotent. Adds:

  - `refineries.commodities`   — staging table mirroring `oil_network.commodities`.
                                 The explorer populates this as it identifies
                                 grades; later reconciliation merges into the
                                 main schema.
  - `refineries.refinery.primary_commodity`   — most-likely slate, defaults to 'crude'.
  - `refineries.slate.commodity` + `.run_id`  — link each slate observation to
                                                 a commodity and the agent run
                                                 that produced it.
  - `refineries.runs_monthly.commodity` + `.run_id` — same for the synthesised
                                                       monthly series.

Run standalone:

    ..\\..\\..\\.venv\\Scripts\\python.exe code\\migrations\\extend_refineries_schema.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
-- 1. Staging commodities table — same column set as oil_network.commodities,
--    plus audit columns recording which exploration run discovered each entry.
CREATE TABLE IF NOT EXISTS refineries.commodities (
    commodity         TEXT PRIMARY KEY,
    description       TEXT,
    attributes        JSONB NOT NULL DEFAULT '{}'::jsonb,
    sweet_sour        TEXT,
    density_class     TEXT,
    api_gravity_min   NUMERIC,
    api_gravity_max   NUMERIC,
    sulfur_pct_min    NUMERIC,
    sulfur_pct_max    NUMERIC,
    region            TEXT,
    typical_basin     TEXT,
    discovered_in_run INTEGER REFERENCES refineries.exploration_runs(run_id) ON DELETE SET NULL,
    discovered_at     TIMESTAMPTZ DEFAULT now()
);

-- 2. Seed the fallback row so primary_commodity defaults can FK to it.
INSERT INTO refineries.commodities (commodity, description)
VALUES ('crude', 'Crude oil (generic root)')
ON CONFLICT (commodity) DO NOTHING;

-- 3. Most-likely slate per refinery. Defaults to 'crude' for refineries the
--    agent hasn't profiled in detail.
ALTER TABLE refineries.refinery
    ADD COLUMN IF NOT EXISTS primary_commodity TEXT
        REFERENCES refineries.commodities(commodity) DEFAULT 'crude';

-- 4. Link slate observations to a canonical commodity and the run that found them.
ALTER TABLE refineries.slate
    ADD COLUMN IF NOT EXISTS commodity TEXT
        REFERENCES refineries.commodities(commodity);
ALTER TABLE refineries.slate
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES refineries.exploration_runs(run_id) ON DELETE SET NULL;

-- 5. Same for the synthesised monthly series.
ALTER TABLE refineries.runs_monthly
    ADD COLUMN IF NOT EXISTS commodity TEXT
        REFERENCES refineries.commodities(commodity) DEFAULT 'crude';
ALTER TABLE refineries.runs_monthly
    ADD COLUMN IF NOT EXISTS run_id INTEGER
        REFERENCES refineries.exploration_runs(run_id) ON DELETE SET NULL;
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        print("refineries schema extended.")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM refineries.commodities;"
            )
            n_commodities = cur.fetchone()[0]
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'refineries'
                  AND table_name   = 'runs_monthly'
                ORDER BY ordinal_position
                """
            )
            cols = [r[0] for r in cur.fetchall()]
        print(f"  refineries.commodities rows: {n_commodities}")
        print(f"  refineries.runs_monthly columns: {cols}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
