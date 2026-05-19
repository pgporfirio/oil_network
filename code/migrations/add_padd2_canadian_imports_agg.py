"""Create `padd2_canadian_imports_agg` — the PADD-2 Canadian-imports aggregate.

Generalises the `padd{1,3,5}_imports_agg` pattern (Principle 2.6: physical-layer
aggregate authoritative, observational view alias-derived) to Canadian inflows
into PADD 2. The aggregate:

  * holds the `MCRIPP2CA2` TS (PADD-2 imports from Canada, ~2,940 kbd)
  * sits between `canadian_oil_sands` and `padd2_view`
  * is a natural future home for per-grade decomposition (heavy sour /
    light synthetic / medium) of Canadian inflows, once grade data lands

Topology after this migration:

    canadian_oil_sands ──[TS: MCRIPP2CA2]──→ padd2_canadian_imports_agg ──→ padd2_view
                                                  (P=C=ΔS=B=0; outflow latent,
                                                   resolved via reverse-mirror)

    padd2_view.inflow ← canadian_oil_sands  =  alias(agg.inflow ← canadian_oil_sands)

Idempotent: creates rows if absent, no-op if present. Reversible by deleting
the new node + variables and restoring the prior padd2_view.inflow assignment.

After running this script, re-run `resolve_scenario.py` to refresh the
resolved values for the starter scenario.
"""

from __future__ import annotations

import sys
import psycopg2
from psycopg2.extras import Json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

AGG_ID = "padd2_canadian_imports_agg"
TS_ID = "eia:MCRIPP2CA2"


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # 1. Create the asset row.
        cur.execute(
            """
            INSERT INTO oil_network.assets
                (asset_id, name, description, node_class, node_subtype, kind,
                 starter_status, attributes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset_id) DO NOTHING
            """,
            (
                AGG_ID,
                "PADD 2 Canadian imports aggregate",
                "Abstract aggregate node that receives Canadian crude flowing into PADD 2 via Enbridge Mainline + Keystone (and any other Canadian routes). Holds the PADD-aggregate TS so the per-pipe split stays a Principle-2.11 latent allocation. Mirrors the padd{1,3,5}_imports_agg pattern for foreign-supply maritime imports.",
                "infrastructure",
                "import_terminal",
                "physical",
                "authoritative",
                Json({
                    "operator": "multiple (Enbridge, TC Energy)",
                    "physical_routes": [
                        "pipe_enbridge_mainline_ca -> clearbrook_entry",
                        "pipe_keystone -> cushing_hub (PADD-2 portion)",
                    ],
                    "future_grades": ["heavy_sour_dilbit", "light_synthetic", "medium"],
                    "purpose": "PADD-2-aggregate TS holder for Canadian imports; routes physically via two latent pipes",
                }),
            ),
        )

        # 2. Create the nodes row.
        cur.execute(
            """
            INSERT INTO oil_network.nodes
                (node_id, graph_id, asset_id, node_type, starter_status, attributes)
            VALUES (%s, (SELECT graph_id FROM oil_network.graphs LIMIT 1),
                    %s, 'import_terminal', 'authoritative', %s)
            ON CONFLICT (node_id) DO NOTHING
            """,
            (AGG_ID, AGG_ID, Json({})),
        )

        # 3. Create the variables (inflow + outflow). The 4 non-relational
        # variables (P, C, S, B) come from node_type_default_formulas via
        # v_effective_assignments, so we don't need to register them in
        # variables either — but they have to exist as base rows in
        # `variables`. Check what other import_terminals do.
        # padd1_imports_agg has all 6 variable types registered. Mirror that.
        cur.execute(
            """
            INSERT INTO oil_network.variables
                (variable_id, variable_type, commodity, node_id, related_node_id, description)
            VALUES
                ('production__crude__' || %s,    'production',    'crude', %s, NULL, NULL),
                ('consumption__crude__' || %s,   'consumption',   'crude', %s, NULL, NULL),
                ('inventory__crude__' || %s,     'inventory',     'crude', %s, NULL, NULL),
                ('balancing_item__crude__' || %s,'balancing_item','crude', %s, NULL, NULL),
                ('inflow__crude__' || %s || '__canadian_oil_sands',
                                                 'inflow',        'crude', %s, 'canadian_oil_sands', NULL),
                ('outflow__crude__' || %s || '__padd2_view',
                                                 'outflow',       'crude', %s, 'padd2_view', NULL)
            ON CONFLICT (variable_id) DO NOTHING
            """,
            (AGG_ID, AGG_ID, AGG_ID, AGG_ID, AGG_ID, AGG_ID, AGG_ID, AGG_ID,
             AGG_ID, AGG_ID, AGG_ID, AGG_ID),
        )

        # 4. Variable assignments for the scenario.
        # The inflow gets the TS. Pass-through scalar variables (P/C/S/B) come
        # from node_type_default_formulas (all '0' for import_terminal). The
        # outflow to padd2_view stays 'latent()' and will be reverse-mirrored
        # to the inflow value (2,940 kbd).
        cur.execute(
            """
            INSERT INTO oil_network.variable_assignments
                (scenario_id, variable_id, effective_from, timeseries_id, formula, formula_inputs, notes)
            VALUES
                (%s, 'inflow__crude__' || %s || '__canadian_oil_sands',
                 '2015-01-01', %s, NULL, ARRAY[]::text[],
                 'PADD-2 Canadian imports authoritative on this aggregate; padd2_view alias-derived'),
                (%s, 'outflow__crude__' || %s || '__padd2_view',
                 '2015-01-01', NULL, 'latent()', ARRAY[]::text[],
                 'Pass-through to padd2_view; reverse-mirror to inflow under import_terminal defaults')
            ON CONFLICT (scenario_id, variable_id, effective_from) DO NOTHING
            """,
            (SCENARIO, AGG_ID, TS_ID, SCENARIO, AGG_ID),
        )

        # 5. Re-point padd2_view.inflow ← canadian_oil_sands to alias from the agg.
        view_var = "inflow__crude__padd2_view__canadian_oil_sands"
        agg_inflow_var = f"inflow__crude__{AGG_ID}__canadian_oil_sands"
        cur.execute(
            """
            UPDATE oil_network.variable_assignments
            SET timeseries_id = NULL,
                formula = %s,
                formula_inputs = ARRAY[%s]::text[],
                notes = COALESCE(notes,'') || ' | repointed 2026-05-13: TS moved to padd2_canadian_imports_agg, this now alias-derived'
            WHERE scenario_id = %s AND variable_id = %s
            """,
            (agg_inflow_var, agg_inflow_var, SCENARIO, view_var),
        )

        conn.commit()
        print(f"Created {AGG_ID} (asset+node+6 variables+2 assignments).")
        print(f"Re-pointed {view_var} -> alias({agg_inflow_var}).")

    print("\nDone. Re-run resolve_scenario.py to refresh resolved values.")


if __name__ == "__main__":
    main()
