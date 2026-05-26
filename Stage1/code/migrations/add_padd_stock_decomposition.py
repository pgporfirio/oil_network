"""Decompose each padd_view.inventory into tank_farms_pipelines + refinery_stocks (+ SPR for PADD3).

The EIA publishes per-PADD stocks split into two buckets that match its
own internal categorisation:

    MCRSTP{N}1  =  MCRSFP{N}1            +  MCRRSP{N}1           [ + SPR if PADD3 ]
                   |                        |                       |
                   tank farms + pipelines   refineries              SPR (only PADD3)

This pass adds two new abstract aggregate nodes per PADD (10 total), each
TS-bound to the matching EIA series. Their `formula_inputs` declare the
known physical constituents (named hubs / gathering / refineries). Since
most named tanks are inventory=latent and per-refinery inventory is not
public, the constraint set on these aggregates is partial — the aggregate
TS gives the value, the constituents document the structural decomposition.

After this pass, `padd{N}_view.inventory.formula_inputs` becomes:
    [padd{N}_tank_farms_pipelines, padd{N}_refinery_stocks, (spr_total)]

with the spr_total only for PADD3. The TS at padd_view stays as
MCRSTP{N}1 (single source of truth at PADD level); the constraint set
documents the EIA bucket identity.

Idempotent: every INSERT uses ON CONFLICT; the formula_inputs UPDATE
re-points wholesale to the new structure (any prior listed constituents
are removed from padd_view.inventory and moved under the new aggregates
where applicable).
"""
from __future__ import annotations

import json
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"
GRAPH = "us_crude_starter"

# Tank-farm + pipeline physical constituents per PADD (named hubs + gathering).
# Per EIA: MCRSFP includes commercial storage hubs + pipeline line-fill.
TANK_FARMS_CONSTITUENTS = {
    1: [],  # No physical storage hubs modelled in PADD1
    2: ["cushing_enbridge", "cushing_enterprise", "cushing_hub", "cushing_plains",
        "patoka_hub", "bakken_nd_gathering", "pipe_bakken_xstate"],
    3: ["corpus_christi_hub", "houston_hub", "nederland_hub", "st_james_hub",
        "eagle_ford_gathering", "permian_nm_gathering", "permian_tx_gathering"],
    4: ["guernsey_hub", "bakken_mt_gathering"],
    5: ["ans_gathering"],
}


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # Pull refineries per PADD
        cur.execute("""
            SELECT l.padd, a.asset_id
            FROM oil_network.assets a JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.node_subtype = 'refinery'
            ORDER BY l.padd, a.asset_id
        """)
        refineries_by_padd = {1: [], 2: [], 3: [], 4: [], 5: []}
        for padd, aid in cur.fetchall():
            n = int(padd[-1])  # 'PADD1' -> 1
            refineries_by_padd[n].append(aid)

        for n in range(1, 6):
            print(f"\n=== PADD {n} ===")
            print(f"  refineries to bucket: {len(refineries_by_padd[n])}")
            print(f"  tank-farm constituents: {len(TANK_FARMS_CONSTITUENTS[n])}")

            tf_id  = f"padd{n}_tank_farms_pipelines"
            rs_id  = f"padd{n}_refinery_stocks"

            for new_id, ts_id, label, kind_label in [
                (tf_id, f"eia:MCRSFP{n}1", f"PADD {n} tank farms + pipelines",  "tank_farms_pipelines"),
                (rs_id, f"eia:MCRRSP{n}1", f"PADD {n} refinery stocks",          "refinery_stocks"),
            ]:
                # Asset
                attrs = {
                    "configuration": {
                        "decomposition_kind": kind_label,
                        "padd": f"PADD{n}",
                        "data_source": f"EIA series {ts_id}",
                    },
                    "resolution_hierarchy": {"parent": f"padd{n}_view", "aggregation": "sum"},
                }
                cur.execute("""
                    INSERT INTO oil_network.assets (asset_id, name, node_class, node_subtype, kind, starter_status, attributes)
                    VALUES (%s, %s, 'observational', 'observational_aggregate', 'abstract', 'authoritative', %s::jsonb)
                    ON CONFLICT (asset_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        node_class = EXCLUDED.node_class,
                        node_subtype = EXCLUDED.node_subtype,
                        kind = EXCLUDED.kind,
                        attributes = EXCLUDED.attributes
                """, (new_id, label, json.dumps(attrs)))

                # Node
                cur.execute("""
                    INSERT INTO oil_network.nodes (node_id, graph_id, asset_id, node_type, starter_status, attributes)
                    VALUES (%s, %s, %s, 'observational_aggregate', 'authoritative', %s::jsonb)
                    ON CONFLICT (graph_id, asset_id) DO UPDATE SET
                        node_type = EXCLUDED.node_type,
                        attributes = EXCLUDED.attributes
                """, (new_id, GRAPH, new_id, json.dumps(attrs)))

                # 4 non-relational variables: P, C, S, B
                for vtype in ("production", "consumption", "inventory", "balancing_item"):
                    vid = f"{vtype}__crude__{new_id}"
                    cur.execute("""
                        INSERT INTO oil_network.variables (variable_id, variable_type, commodity, node_id)
                        VALUES (%s, %s, 'crude', %s)
                        ON CONFLICT (variable_id) DO NOTHING
                    """, (vid, vtype, new_id))

                # Variable assignments:
                #   inventory: TS-bound to the EIA series + formula_inputs declares constituents
                #   P, C, B:   zero by construction (observational aggregates don't produce/consume/balance)
                inv_id = f"inventory__crude__{new_id}"
                if "tank_farms_pipelines" in new_id:
                    constituents = [f"inventory__crude__{c}" for c in TANK_FARMS_CONSTITUENTS[n]]
                else:  # refinery_stocks
                    constituents = [f"inventory__crude__{r}" for r in refineries_by_padd[n]]

                cur.execute("""
                    INSERT INTO oil_network.variable_assignments
                        (variable_id, scenario_id, effective_from, timeseries_id, formula, formula_inputs)
                    VALUES (%s, %s, '0001-01-01'::DATE, %s, NULL, %s)
                    ON CONFLICT (variable_id, scenario_id, effective_from) DO UPDATE SET
                        timeseries_id = EXCLUDED.timeseries_id,
                        formula = EXCLUDED.formula,
                        formula_inputs = EXCLUDED.formula_inputs
                """, (inv_id, SCENARIO, ts_id, constituents))

                for vtype in ("production", "consumption", "balancing_item"):
                    vid = f"{vtype}__crude__{new_id}"
                    cur.execute("""
                        INSERT INTO oil_network.variable_assignments
                            (variable_id, scenario_id, effective_from, timeseries_id, formula, formula_inputs)
                        VALUES (%s, %s, '0001-01-01'::DATE, NULL, '0', '{}'::text[])
                        ON CONFLICT (variable_id, scenario_id, effective_from) DO UPDATE SET
                            timeseries_id = NULL, formula = '0', formula_inputs = '{}'::text[]
                    """, (vid, SCENARIO))

                # Mark as balance role (partition cell of padd{N}_view)
                cur.execute("""
                    INSERT INTO oil_network.scenario_node_role (scenario_id, node_id, role, notes)
                    VALUES (%s, %s, 'balance',
                            'PADD-level stock-decomposition aggregate; TS-bound; partition child of padd_view.')
                    ON CONFLICT (scenario_id, node_id) DO UPDATE SET
                        role = EXCLUDED.role, notes = EXCLUDED.notes
                """, (SCENARIO, new_id))

                print(f"  + {new_id:38s} ts={ts_id}  constituents={len(constituents)}")

            # Rewire padd{N}_view.inventory.formula_inputs:
            #   replace prior children with [tank_farms_pipelines, refinery_stocks, (spr_total for PADD3)]
            new_inputs = [
                f"inventory__crude__padd{n}_tank_farms_pipelines",
                f"inventory__crude__padd{n}_refinery_stocks",
            ]
            if n == 3:
                new_inputs.append("inventory__crude__spr_total")

            padd_inv_var = f"inventory__crude__padd{n}_view"
            cur.execute("""
                UPDATE oil_network.variable_assignments
                SET formula_inputs = %s
                WHERE variable_id = %s AND scenario_id = %s
            """, (new_inputs, padd_inv_var, SCENARIO))
            print(f"  padd{n}_view.inventory.formula_inputs = {new_inputs}")

        conn.commit()
        print("\nDone. Re-run refresh_views.py --structural + resolve_scenario.py + audits.")


if __name__ == "__main__":
    main()
