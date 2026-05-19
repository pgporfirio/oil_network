"""Raise production capacities to historical peaks observed in 2015-2024 EIA data.

Original capacity research (patch_pipeline_production_capacities.py) used
2024-current values for some production nodes. The EIA time series carries
2015-2024 history, and several nodes had peaks substantially above 2024:
California (peaked ~565 kbpd in 2015 vs 285 today), Oklahoma (624 vs 395),
Bakken-ND (1499 vs 1180), etc.

For LP-defensible bounds we want max(rated, historical_peak * 1.05). EIA
doesn't publish "rated production capacity" for upstream assets anyway, so
the 5%-buffered historical peak IS the operative cap.

This patch:
  1. Queries scenario_resolved_values for each production variable's peak.
  2. Compares against the current capacity_bpd in assets.configuration.
  3. Updates JSONB with max(current, peak*1.05), rounded up to nearest 10 kbpd.
  4. Carries production_capacity_peak_kbd + production_capacity_peak_date + source.

Idempotent. After running, re-seed via populate_asset_capacities.py.
"""
from __future__ import annotations
import json
import math
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

# Apply to physical producers only — not refineries/pipelines (their production = 0 always).
PRODUCER_SUBTYPES = (
    'state_sub_basin', 'state_conventional', 'state_residual',
    'offshore_region', 'foreign_production_aggregate',
)

# Skip canadian_oil_sands: its 5 Mbpd rated cap is Canadian-side production, not
# US-bound flow. Its resolved production variable is a sum_over_outflows of the
# pipe inflows, so the peak (~776 kbpd) reflects flow into the US, not capacity.
SKIP = {'canadian_oil_sands'}


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # Pull peak per producer-asset's production variable
        cur.execute("""
            SELECT a.asset_id,
                   max(srv.value)                                              AS peak_kbd,
                   (array_agg(srv.observation_date ORDER BY srv.value DESC NULLS LAST))[1] AS peak_date,
                   (a.attributes->'configuration'->>'production_capacity_bd')::DOUBLE PRECISION AS current_bd
            FROM oil_network.assets a
            JOIN oil_network.variables v
              ON v.node_id = a.asset_id AND v.variable_type = 'production' AND v.commodity = 'crude'
            JOIN oil_network.scenario_resolved_values srv
              ON srv.variable_id = v.variable_id
             AND srv.scenario_id = 'starter_us_crude_2015_2025'
            WHERE a.node_subtype = ANY(%s)
              AND srv.value IS NOT NULL
            GROUP BY a.asset_id, a.attributes->'configuration'->>'production_capacity_bd'
        """, (list(PRODUCER_SUBTYPES),))

        rows = cur.fetchall()
        print(f"{'asset':30s} {'peak_kbd':>10s} {'cur_bd':>12s} {'rec_bd':>12s} {'action':>10s}")
        print('-' * 80)

        n_updated = 0
        n_unchanged = 0
        for asset_id, peak_kbd, peak_date, cur_bd in rows:
            if asset_id in SKIP:
                continue
            if peak_kbd is None or peak_kbd <= 0:
                continue
            rec_kbd = math.ceil(peak_kbd * 1.05 / 10) * 10
            rec_bd  = int(rec_kbd * 1000)
            cur_bd_i = int(cur_bd) if cur_bd else 0

            if rec_bd > cur_bd_i:
                action = 'RAISE'
                patch = {
                    "production_capacity_bd": rec_bd,
                    "production_capacity_peak_kbd": float(peak_kbd),
                    "production_capacity_peak_date": peak_date.isoformat() if peak_date else None,
                    "production_capacity_basis": "1.05x historical peak in EIA series 2015-2024",
                    "production_capacity_bd_source": "research_pass_2026_05_15 + historical-peak override",
                }
                cur.execute("""
                    UPDATE oil_network.assets
                    SET attributes = jsonb_set(
                        attributes, '{configuration}',
                        COALESCE(attributes->'configuration', '{}'::jsonb) || %s::jsonb
                    )
                    WHERE asset_id = %s
                """, (json.dumps(patch), asset_id))
                n_updated += 1
            else:
                action = 'keep'
                n_unchanged += 1
            print(f"{asset_id:30s} {peak_kbd:>10.1f} {cur_bd_i:>12d} {rec_bd:>12d} {action:>10s}")

        conn.commit()
        print(f"\nDone. {n_updated} producers raised; {n_unchanged} already adequate.")


if __name__ == "__main__":
    main()
