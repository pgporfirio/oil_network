"""Create `oil_network.asset_capacities` — physical capacity facts per asset.

Schema slot for the *physical* capacity of each asset. Scenario-agnostic
(the refinery's nameplate doesn't change because we chose a different
coverage contract). Per-commodity (a tank can hold crude vs products; a
refinery's crude-input capacity is different from its product-output
capacity).

Two layers, made explicit:

  asset_capacities      -> physical reality of the asset, per commodity
                           ('capacity_physical' for refinery throughput, pipe
                           rated capacity, tank size, basin production cap).
                           Migrated out of assets.attributes->configuration.

  variable_constraints  -> scenario-specific overlays. Deratings, commercial
                           limits, contractual caps. Populated only when a
                           scenario actually needs to override the physical
                           default.

The LP exporter / audit reads via `v_effective_constraints`, which joins
both layers with the overlay winning when present.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
CREATE TABLE IF NOT EXISTS oil_network.asset_capacities (
    asset_id        TEXT NOT NULL  REFERENCES oil_network.assets(asset_id)        ON DELETE CASCADE,
    commodity       TEXT NOT NULL  REFERENCES oil_network.commodities(commodity)  ON DELETE CASCADE,
    capacity_kind   TEXT NOT NULL,
    effective_from  DATE NOT NULL DEFAULT '0001-01-01',
    min_value       DOUBLE PRECISION,
    max_value       DOUBLE PRECISION,
    unit            TEXT NOT NULL,
    source          TEXT,
    notes           TEXT,
    PRIMARY KEY (asset_id, commodity, capacity_kind, effective_from),
    CHECK (capacity_kind IN ('throughput','storage','consumption','production')),
    CHECK (min_value IS NOT NULL OR max_value IS NOT NULL),
    CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
);

CREATE INDEX IF NOT EXISTS ix_asset_capacities_asset
    ON oil_network.asset_capacities (asset_id, commodity, effective_from);
CREATE INDEX IF NOT EXISTS ix_asset_capacities_kind
    ON oil_network.asset_capacities (capacity_kind);

COMMENT ON TABLE oil_network.asset_capacities IS
'Physical capacity per (asset, commodity, capacity_kind). Scenario-agnostic '
'and time-versioned via effective_from. capacity_kind takes one of '
'{throughput, storage, consumption, production}, each mapping to a particular '
'variable type. The LP exporter and audit_capacity_violations.py read this '
'(via v_effective_constraints which also picks up scenario overrides from '
'variable_constraints).';
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
    print("asset_capacities table created (or already existed)")


if __name__ == "__main__":
    main()
