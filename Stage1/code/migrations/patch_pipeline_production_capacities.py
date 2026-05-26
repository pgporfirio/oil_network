"""Patch assets.attributes->configuration with researched capacities.

Adds `capacity_bpd` to 19 pipelines and `production_capacity_bd` to 19 production
nodes that were missing capacity data. Researched 2026-05-15 from EIA, company
filings, Wikipedia citations, Energy Information sources. See HANDOVER.md
twenty-first-pass entry for the research subagent's full citation list.

Idempotent: UPDATE merges keys into the existing configuration object.
After running, re-run `populate_asset_capacities.py` to materialise the new
capacities into `oil_network.asset_capacities`.

Notes flagged by the researcher:
  - pipe_capline: REVERSED in Jan 2022 — now south-bound Patoka -> St James only.
  - pipe_seaway: south-bound only since 2012 reversal.
  - pipe_loop_connector: bidirectional since 2018.
  - pipe_dapl_etco: 1.1 Mbpd expansion approved but not commissioned — using 750 kbpd current.
  - pipe_taps: 2.1 Mbpd nameplate; actual ~465 kbpd 2024.
  - pipe_harvest_eagle_ford: actual operator Harvest Midstream/Hilcorp (not ExxonMobil JV).
  - pipe_locap: ambiguous between LOCAP (1.7 Mbpd) and Ship Shoal (390 kbpd) — used LOCAP.
  - PADD residuals: arithmetic estimates from EIA PADD-level production minus
    named producers; padd2_other / padd3_other are rough.
"""
from __future__ import annotations

import json

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


PIPELINE_CAPACITIES_BPD = {
    "pipe_basin":             550_000,
    "pipe_bridgetex":         440_000,
    "pipe_cactus_ii":         670_000,
    "pipe_capline":           300_000,   # reversed in 2022 — south-bound only
    "pipe_dapl_etco":         750_000,   # 1.1 Mbpd expansion approved, not commissioned
    "pipe_enbridge_mainline": 600_000,   # US portion = Flanagan South
    "pipe_epic_crude":        600_000,
    "pipe_gray_oak":          900_000,
    "pipe_harvest_eagle_ford":380_000,   # operator: Harvest Midstream (not ExxonMobil)
    "pipe_kmcc":              350_000,
    "pipe_locap":           1_700_000,   # LOCAP connector, not Ship Shoal
    "pipe_longhorn":          275_000,
    "pipe_loop_connector":  1_200_000,   # bidirectional since 2018
    "pipe_marketlink":        750_000,
    "pipe_midland_to_echo":   620_000,
    "pipe_seaway":            950_000,   # south-bound only
    "pipe_spearhead":         190_000,
    "pipe_taps":            2_100_000,   # design nameplate, 1988
    "pipe_wink_to_webster": 1_500_000,
}

PRODUCTION_CAPACITIES_BD = {
    # Aggregate / boundary
    "foreign_supply":          2_500_000,
    # US offshore + state-level
    "gulf_of_america":         1_800_000,
    "alaska_north_slope":        490_000,
    "california_conventional":   300_000,
    "colorado_conventional":     500_000,
    "oklahoma_conventional":     420_000,
    "wyoming_conventional":      300_000,
    "montana_other":              25_000,
    "texas_other":               200_000,
    # Sub-basin (state portion of named basin)
    "bakken_mt":                  40_000,
    "bakken_nd":               1_200_000,
    "eagle_ford_tx":           1_200_000,
    "permian_nm":              2_000_000,
    "permian_tx":              4_300_000,
    # PADD residuals (production NOT in named producers above)
    "padd1_other":                20_000,
    "padd2_other":               250_000,
    "padd3_other":               200_000,
    "padd4_other":               100_000,
    "padd5_other":                50_000,
}

SOURCE = "research_pass_2026_05_15"


def patch_config(cur, asset_id: str, key: str, value: int) -> bool:
    """Merge {key: value, key_source: SOURCE} into assets.attributes->configuration.
    Returns True if a row was updated."""
    patch = {key: value, f"{key}_source": SOURCE}
    cur.execute("""
        UPDATE oil_network.assets
        SET attributes = jsonb_set(
            attributes,
            '{configuration}',
            COALESCE(attributes->'configuration', '{}'::jsonb) || %s::jsonb
        )
        WHERE asset_id = %s
        RETURNING asset_id
    """, (json.dumps(patch), asset_id))
    return cur.fetchone() is not None


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("=== Patching pipeline capacities ===")
        n_pipe = 0
        for asset_id, cap in PIPELINE_CAPACITIES_BPD.items():
            if patch_config(cur, asset_id, "capacity_bpd", cap):
                n_pipe += 1
                print(f"  {asset_id:32s} capacity_bpd = {cap:>10,}")
            else:
                print(f"  ! {asset_id:32s} NOT FOUND in assets table — skipped")

        print()
        print("=== Patching production capacities ===")
        n_prod = 0
        for asset_id, cap in PRODUCTION_CAPACITIES_BD.items():
            if patch_config(cur, asset_id, "production_capacity_bd", cap):
                n_prod += 1
                print(f"  {asset_id:30s} production_capacity_bd = {cap:>10,}")
            else:
                print(f"  ! {asset_id:30s} NOT FOUND in assets table — skipped")

        conn.commit()
        print()
        print(f"Done. {n_pipe} pipelines + {n_prod} production nodes patched.")
        print("Next: re-run populate_asset_capacities.py to materialise into asset_capacities table.")


if __name__ == "__main__":
    main()
