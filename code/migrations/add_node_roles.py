"""Populate `oil_network.scenario_node_role` for the starter scenario.

Per-scenario, per-node tagging of abstract aggregates:

  role = 'balance'    -> partition cell in this scenario's active partition.
                          Sum-of-children = self is a hard identity per variable_type.
  role = 'constraint' -> auxiliary observation or boundary node. Provides extra
                          equality/inequality constraints but NOT summed into the
                          partition. Used to bound latents.

Physical nodes don't need rows here — they're implicit partition leaves of whichever
balance node owns them.

For starter_us_crude_2015_2025 (geographic primary), the classification is:

  BALANCE (16 abstract nodes):
    - usa_view                          national partition root
    - padd1..5_view                     5 PADD partition cells
    - district_R*_refining_view × 10    sub-PADD refining-district partition cells

  CONSTRAINT (10 abstract nodes):
    - permian, bakken, eagle_ford       basin-level auxiliary observations
                                          (re-slice of physical basin-state nodes
                                          that are already inside PADD partitions)
    - spr_total                         SPR rollup — already counted inside
                                          padd3_view.S (MCRSTP31 includes SPR)
    - usa_lower48_excl_gom_view         STEO subtotal — auxiliary cross-check
    - texas_state_view, montana_state_view  state-level auxiliary observations
    - canadian_oil_sands, foreign_supply    boundary sources
    - foreign_export_destination            boundary sink

Creates the table if missing (idempotent). Re-runs UPSERT.
"""
from __future__ import annotations
import sys
import psycopg2

sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

BALANCE_NODES = [
    "usa_view",
    "padd1_view", "padd2_view", "padd3_view", "padd4_view", "padd5_view",
    "district_REC_refining_view", "district_RAP_refining_view",
    "district_R2A_refining_view", "district_R2B_refining_view", "district_R2C_refining_view",
    "district_R3A_refining_view", "district_R3B_refining_view", "district_R3C_refining_view",
    "district_R3D_refining_view", "district_R3E_refining_view",
]

CONSTRAINT_NODES = [
    "permian", "bakken", "eagle_ford",
    "spr_total",
    "usa_lower48_excl_gom_view",
    "texas_state_view", "montana_state_view",
    "canadian_oil_sands", "foreign_supply", "foreign_export_destination",
]


DDL = """
CREATE TABLE IF NOT EXISTS oil_network.scenario_node_role (
    scenario_id TEXT NOT NULL REFERENCES oil_network.scenarios(scenario_id) ON DELETE CASCADE,
    node_id     TEXT NOT NULL REFERENCES oil_network.nodes(node_id)         ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('balance', 'constraint')),
    notes       TEXT,
    PRIMARY KEY (scenario_id, node_id)
);
CREATE INDEX IF NOT EXISTS ix_snr_scenario ON oil_network.scenario_node_role(scenario_id);
CREATE INDEX IF NOT EXISTS ix_snr_role     ON oil_network.scenario_node_role(role);
"""

UPSERT = """
INSERT INTO oil_network.scenario_node_role (scenario_id, node_id, role, notes)
VALUES (%s, %s, %s, %s)
ON CONFLICT (scenario_id, node_id) DO UPDATE
    SET role  = EXCLUDED.role,
        notes = EXCLUDED.notes;
"""


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        # Validate every node exists in the asset graph before tagging
        cur.execute("SELECT asset_id FROM oil_network.assets")
        existing = {r[0] for r in cur.fetchall()}

        rows = []
        for nid in BALANCE_NODES:
            if nid not in existing:
                print(f"  WARN: balance node {nid!r} not in asset graph, skipping")
                continue
            rows.append((SCENARIO, nid, "balance",
                         "Partition cell in the active geographic-primary partition."))
        for nid in CONSTRAINT_NODES:
            if nid not in existing:
                print(f"  WARN: constraint node {nid!r} not in asset graph, skipping")
                continue
            rows.append((SCENARIO, nid, "constraint",
                         "Auxiliary observation or boundary node — not in partition sum."))

        cur.executemany(UPSERT, rows)
        conn.commit()
        print(f"upserted {len(rows)} rows into scenario_node_role for scenario {SCENARIO!r}")

        # Summary
        cur.execute("""
            SELECT role, COUNT(*) FROM oil_network.scenario_node_role
            WHERE scenario_id=%s GROUP BY role ORDER BY role
        """, (SCENARIO,))
        for role, n in cur.fetchall():
            print(f"  {role:12s} {n}")


if __name__ == "__main__":
    main()
