"""Populate oil_network.node_type_default_formulas with structural defaults.

Encodes "which mass-balance terms are zero by construction for each node type",
so that any variable on any scenario without an explicit override in
oil_network.variable_assignments inherits the right default.

Concept:
  - 0           = structurally zero (no crude ever produced/consumed/held here)
  - latent()    = unknown but possibly non-zero; awaits override or stays latent
  - sum         = aggregation node (canonical sum-over-formula_inputs; the
                  twelfth pass collapsed sum_over_children / sum_over_outflows
                  / sum_same_type into this one label)

Relational variables (inflow / outflow per edge) are intentionally NOT covered
here — they default to latent() universally, and per-edge bindings come from
assign_eia (TS-bound observations) or per-scenario overrides.

Idempotent: UPSERT on the (node_type, variable_type) primary-uniqueness pair.
"""
from __future__ import annotations

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

# (node_type, variable_type, default_formula, description)
# Commodity column left NULL — applies to every commodity (currently just crude).
DEFAULTS: list[tuple[str, str, str, str]] = [
    # ---------- Pass-through node types: B=0 closes mass balance --------------
    ("gathering", "production",     "0",        "Gathering systems do not produce crude — they aggregate basin output."),
    ("gathering", "consumption",    "0",        "Gathering systems do not consume crude."),
    ("gathering", "inventory",      "0",        "No buffer modelled at gathering scale."),
    ("gathering", "balancing_item", "0",        "Pure pass-through: B=0 forces ΣI = ΣO at the gathering."),

    ("origin_terminal", "production",     "0",   "Origin terminals route crude; they do not produce it."),
    ("origin_terminal", "consumption",    "0",   "Origin terminals do not consume crude."),
    ("origin_terminal", "inventory",      "0",   "No buffer modelled at origin-terminal scale."),
    ("origin_terminal", "balancing_item", "0",   "Pure pass-through: B=0 forces ΣI = ΣO."),

    ("pipeline", "production",     "0",          "Pipelines do not produce crude."),
    ("pipeline", "consumption",    "0",          "Pipelines do not consume crude."),
    ("pipeline", "inventory",      "0",          "Line-fill not modelled at this scale."),
    ("pipeline", "balancing_item", "0",          "Pure pass-through: B=0 forces ΣI = ΣO."),

    ("import_terminal", "production",     "0",   "Import terminals route foreign crude in; they do not produce it."),
    ("import_terminal", "consumption",    "0",   "Import terminals do not consume crude."),
    ("import_terminal", "inventory",      "0",   "No buffer modelled at import-terminal scale."),
    ("import_terminal", "balancing_item", "0",   "Pure pass-through: B=0 forces ΣI = ΣO."),

    ("export_terminal", "production",     "0",   "Export terminals route crude out; they do not produce it."),
    ("export_terminal", "consumption",    "0",   "Export terminals do not consume crude."),
    ("export_terminal", "inventory",      "0",   "No buffer modelled at export-terminal scale."),
    ("export_terminal", "balancing_item", "0",   "Pure pass-through: B=0 forces ΣI = ΣO."),

    ("foreign_export_destination", "production",     "0", "Foreign export destination is a boundary sink."),
    ("foreign_export_destination", "consumption",    "0", "Boundary sink — consumption outside our scope."),
    ("foreign_export_destination", "inventory",      "0", "Boundary sink — no inventory modelled."),
    ("foreign_export_destination", "balancing_item", "0", "Boundary sink — no adjustment."),

    ("foreign_production_aggregate", "production",     "latent()", "Boundary source — P bound via TS override (canadian_oil_sands)."),
    ("foreign_production_aggregate", "consumption",    "0",        "Boundary source — no consumption."),
    ("foreign_production_aggregate", "inventory",      "0",        "Boundary source — no inventory modelled."),
    ("foreign_production_aggregate", "balancing_item", "0",        "Trust EIA's foreign-supply reporting at boundary — no adjustment."),

    # ---------- Accumulators: keep B latent (buffer behaviour possible) ------
    ("storage_terminal", "production",     "0",        "Storage terminals do not produce crude."),
    ("storage_terminal", "consumption",    "0",        "Storage terminals do not consume crude."),
    ("storage_terminal", "inventory",      "latent()", "Buffer — S observed only at Cushing in starter scenario; rest latent."),
    ("storage_terminal", "balancing_item", "latent()", "Hubs may carry small statistical adjustment beyond inventory delta."),

    ("spr_site", "production",     "0",        "SPR sites do not produce crude."),
    ("spr_site", "consumption",    "0",        "SPR sites do not consume crude."),
    ("spr_site", "inventory",      "latent()", "S observed via EIA PSM Table 38 (TS-bound override per site)."),
    ("spr_site", "balancing_item", "latent()", "SPR-level statistical adjustment possible."),

    ("refinery", "production",     "0",        "Refineries do not produce crude — they consume it (refined product outside scope)."),
    ("refinery", "consumption",    "latent()", "C bound per-refinery from observed data when available."),
    ("refinery", "inventory",      "latent()", "Refinery operating stocks — observed at PADD level, latent per-asset."),
    ("refinery", "balancing_item", "latent()", "Refinery-level statistical adjustment possible."),

    # ---------- Physical sources ---------------------------------------------
    ("state_sub_basin", "production",     "latent()", "P set by arithmetic override (e.g. permian_tx = permian − permian_nm)."),
    ("state_sub_basin", "consumption",    "0",        "Production-side node — no consumption."),
    ("state_sub_basin", "inventory",      "0",        "Production-side node — no inventory at this resolution."),
    ("state_sub_basin", "balancing_item", "latent()", "State-level production reporting can have small adjustments."),

    ("state_conventional", "production",     "latent()", "P bound via TS (state-level EIA MCRFP*2 series)."),
    ("state_conventional", "consumption",    "0",        "Production-side node — no consumption."),
    ("state_conventional", "inventory",      "0",        "Production-side node — no inventory at this resolution."),
    ("state_conventional", "balancing_item", "latent()", "State-level production reporting can have small adjustments."),

    ("offshore_region", "production",     "latent()", "P bound via TS (e.g. Gulf of America MCRFP3FM2)."),
    ("offshore_region", "consumption",    "0",        "Production-side node — no consumption."),
    ("offshore_region", "inventory",      "0",        "Production-side node — no inventory at this resolution."),
    ("offshore_region", "balancing_item", "latent()", "Federal-offshore reporting can have small adjustments."),

    # ---------- Aggregate / view node types -----------------------------------
    ("observational_aggregate", "production",     "sum",               "Basin views aggregate sub-basin production."),
    ("observational_aggregate", "consumption",    "0",                 "Production-side aggregates — no consumption."),
    ("observational_aggregate", "inventory",      "latent()",          "Stocks latent unless aggregate (e.g. spr_total) has a TS override."),
    ("observational_aggregate", "balancing_item", "0",                 "Aggregate views — adjustment captured at constituent level."),

    ("state_residual", "production",     "latent()", "P = arithmetic residual (state − modelled basins) — per-node override."),
    ("state_residual", "consumption",    "0",        "Residual production node — no consumption."),
    ("state_residual", "inventory",      "0",        "Residual production node — no inventory."),
    ("state_residual", "balancing_item", "0",        "Residual computed exactly by construction — no adjustment."),

    ("state_view", "production",     "latent()", "P bound via state-level TS override (e.g. MCRFPTX2)."),
    ("state_view", "consumption",    "0",        "State-level production view — no consumption."),
    ("state_view", "inventory",      "0",        "Production-side view — no inventory."),
    ("state_view", "balancing_item", "0",        "Aggregate state view — adjustment captured downstream."),

    ("usa_subtotal_view", "production",     "latent()", "P bound via TS override (e.g. PAPR48NGOM for lower-48 excl. GoM)."),
    ("usa_subtotal_view", "consumption",    "0",        "Production-side subtotal — no consumption."),
    ("usa_subtotal_view", "inventory",      "0",        "Production-side subtotal — no inventory."),
    ("usa_subtotal_view", "balancing_item", "0",        "Aggregate view — adjustment captured downstream."),

    ("refining_district_view", "production",     "0",        "District views aggregate refineries — no production."),
    ("refining_district_view", "consumption",    "latent()", "C bound via TS (M_EPC0_YIY_R*) per district."),
    ("refining_district_view", "inventory",      "0",        "No district-level stock modelled."),
    ("refining_district_view", "balancing_item", "0",        "District view — adjustment captured at PADD level."),

    ("region_view", "production",     "latent()", "P set by sum_over_children of constituent basins / states (override)."),
    ("region_view", "consumption",    "latent()", "C set by sum_over_children of constituent districts / refineries (override)."),
    ("region_view", "inventory",      "latent()", "S bound via TS (MCRSTP*) per region."),
    ("region_view", "balancing_item", "latent()", "B bound via TS (MCRUA_*) per region in starter; falls back to closure if no override."),
]


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO oil_network.node_type_default_formulas
                (node_type, variable_type, commodity, default_formula, description)
            VALUES (%s, %s, NULL, %s, %s)
            ON CONFLICT (node_type, variable_type) WHERE commodity IS NULL
            DO UPDATE SET
                default_formula = EXCLUDED.default_formula,
                description     = EXCLUDED.description
            """,
            DEFAULTS,
        )
        conn.commit()

        # Verify
        cur.execute("""
            SELECT node_type, COUNT(*) AS n_defaults
            FROM oil_network.node_type_default_formulas
            GROUP BY node_type
            ORDER BY node_type
        """)
        rows = cur.fetchall()

    print(f"populated {len(DEFAULTS)} default rows across {len(rows)} node types:")
    for node_type, n in rows:
        print(f"  {node_type:<32}  {n} defaults")


if __name__ == "__main__":
    main()
