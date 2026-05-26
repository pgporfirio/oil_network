"""Refactor: fold the Jones Act in-transit residual into inter_padd_3_to_5_agg.

The first attempt (add_padd5_jones_act_in_transit.py) created a parallel
abstract observational_aggregate node. But the framework already has the
inter_padd_3_to_5_agg node as a `pipeline`-subtype with proper flow variables:

    inter_padd_3_to_5_agg:
        inflow  <- padd3_view         (mirror of padd3's outflow to this corridor)
        outflow -> padd5_view         (TS: eia:combined_inter_padd_P3_to_P5_kbd)
        inventory                     (currently forced to 0 by pipeline default)

Conceptually that node IS the Jones Act corridor — flows are TS-observed, the
node has the right physical interpretation. It just lacks a line-fill inventory.

This pass:
  1. Override inter_padd_3_to_5_agg.inventory:
        formula = padd5_view - padd5_tank_farms_pipelines - padd5_refinery_stocks
     (arithmetic residual = the in-transit line-fill EIA bundles into PADD5).
  2. Remove padd5_jones_act_in_transit from padd5_view.inventory.formula_inputs.
  3. Add inter_padd_3_to_5_agg to padd5_view.inventory.formula_inputs.
  4. Drop the now-orphan padd5_jones_act_in_transit node (cascade).

This aligns the model with Principle 2.1 (asset-centric): the in-transit
fleet IS the same physical entity that handles the inter-PADD flow, so its
inventory belongs on that node, not a separate aggregate.

Idempotent.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

INV_VAR    = "inventory__crude__inter_padd_3_to_5_agg"
PADD5_INV  = "inventory__crude__padd5_view"
OLD_NODE   = "padd5_jones_act_in_transit"
OLD_INV    = f"inventory__crude__{OLD_NODE}"

FORMULA = ("inventory__crude__padd5_view"
           " - inventory__crude__padd5_tank_farms_pipelines"
           " - inventory__crude__padd5_refinery_stocks")
INPUTS  = [
    "inventory__crude__padd5_view",
    "inventory__crude__padd5_tank_farms_pipelines",
    "inventory__crude__padd5_refinery_stocks",
]


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # 1. Rewrite inter_padd_3_to_5_agg.inventory as the arithmetic residual.
        cur.execute("""
            INSERT INTO oil_network.variable_assignments
                (variable_id, scenario_id, effective_from, timeseries_id, formula, formula_inputs)
            VALUES (%s, %s, '0001-01-01'::DATE, NULL, %s, %s)
            ON CONFLICT (variable_id, scenario_id, effective_from) DO UPDATE SET
                timeseries_id  = NULL,
                formula        = EXCLUDED.formula,
                formula_inputs = EXCLUDED.formula_inputs
        """, (INV_VAR, SCENARIO, FORMULA, INPUTS))
        print(f"  inter_padd_3_to_5_agg.inventory := {FORMULA[:60]}...")

        # 2. Rewire padd5_view.inventory.formula_inputs: remove OLD_INV, add INV_VAR
        cur.execute("""SELECT formula_inputs FROM oil_network.variable_assignments
                       WHERE variable_id=%s AND scenario_id=%s""",
                    (PADD5_INV, SCENARIO))
        current = list((cur.fetchone() or [None])[0] or [])
        new = [x for x in current if x != OLD_INV]
        if INV_VAR not in new:
            new.append(INV_VAR)
        cur.execute("""UPDATE oil_network.variable_assignments
                       SET formula_inputs = %s
                       WHERE variable_id=%s AND scenario_id=%s""",
                    (new, PADD5_INV, SCENARIO))
        print(f"  padd5_view.inventory.formula_inputs := {new}")

        # 3. Drop the old standalone node. Must delete in FK-respecting order:
        #    variable_assignments (cascades via variables FK) -> variables ->
        #    scenario_node_role -> nodes -> assets. Or just delete nodes first
        #    (which cascades variables -> variable_assignments -> resolved_values)
        #    then delete assets. Both routes work; doing it in dependency order
        #    here for clarity.
        cur.execute("""DELETE FROM oil_network.nodes WHERE asset_id=%s""", (OLD_NODE,))
        n_nodes = cur.rowcount
        cur.execute("""DELETE FROM oil_network.scenario_node_role WHERE node_id=%s AND scenario_id=%s""",
                    (OLD_NODE, SCENARIO))
        n_roles = cur.rowcount
        cur.execute("""DELETE FROM oil_network.assets WHERE asset_id=%s""", (OLD_NODE,))
        n_assets = cur.rowcount
        print(f"  dropped {OLD_NODE}: {n_assets} assets / {n_nodes} nodes / {n_roles} role rows")

        # 4. Make sure inter_padd_3_to_5_agg has role = balance so it appears
        #    as a partition child of padd5_view in the balance UI.
        cur.execute("""
            INSERT INTO oil_network.scenario_node_role(scenario_id, node_id, role, notes)
            VALUES (%s, 'inter_padd_3_to_5_agg', 'balance',
                    'Jones Act WB->WC corridor; line-fill = MCRSTP51 - MCRSFP51 - MCRRSP51; partition child of padd5_view.inventory.')
            ON CONFLICT (scenario_id, node_id) DO UPDATE SET
                role = EXCLUDED.role, notes = EXCLUDED.notes
        """, (SCENARIO,))

        conn.commit()
        print("Done. Re-run refresh_views.py --structural + resolve_scenario.py.")


if __name__ == "__main__":
    main()
