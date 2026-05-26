"""Build the `products` schema — staging area for the oil-products tree.

A separate schema from `oil_network` that holds a full IEA / JODI taxonomy of
oil products (primary + secondary). Acts as a staging registry that will
eventually be promoted into `oil_network.commodities` + the existing
`oil_network.commodity_hierarchy` once we wire refined-product variables
through the resolver (see Section 1.4 of the thesis — "grade decomposition +
refined-product yield extensions").

Kept deliberately outside `oil_network` so the framework's structural
invariants (every commodity must have a working set of variable bindings)
remain undisturbed until we are ready to bind these products in.

Objects created (idempotent):

  - SCHEMA   products
  - TABLE    products.oil_products
                (product_code, parent_code, name, description, category,
                 iea_code, jodi_code, state_of_matter, sort_order,
                 typical_density_kg_m3_min/max, boiling_range_c_min/max,
                 attributes JSONB, source, created_at)
  - VIEW     products.v_oil_products_tree
                Recursive descent of the tree with depth + ancestor path.
  - VIEW     products.v_oil_products_jodi_rollup
                One row per JODI bucket showing which IEA-level products
                roll up into it (useful when later mapping observed JODI
                series onto the finer IEA leaves).

Taxonomy source: IEA *Energy Statistics Manual* / IEA *Oil Information*
product definitions, cross-checked against the JODI-Oil World Database
product list (https://www.jodidata.org/oil). Descriptions are paraphrased
from the IEA glossary; precise specs (density / boiling range) follow the
indicative bands published in the same manual.

Re-running this script is a no-op for both DDL and data. Run via:

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\build_products_schema.py
"""
from __future__ import annotations

import json

import psycopg2
from psycopg2.extras import execute_values

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


SCHEMA_DDL = r"""
CREATE SCHEMA IF NOT EXISTS products;

COMMENT ON SCHEMA products IS
    'Staging registry for the full oil-products tree (IEA / JODI taxonomy). '
    'Populated by code/migrations/build_products_schema.py. Will be promoted '
    'into oil_network.commodities + oil_network.commodity_hierarchy once the '
    'framework is extended to bind refined-product variables (Section 1.4 of '
    'the thesis). Kept in a separate schema so the staged tree does not '
    'pollute the working commodity registry.';

CREATE TABLE IF NOT EXISTS products.oil_products (
    product_code      TEXT PRIMARY KEY,
    parent_code       TEXT REFERENCES products.oil_products(product_code) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT,
    category          TEXT,
    iea_code          TEXT,
    jodi_code         TEXT,
    state_of_matter   TEXT,
    typical_density_kg_m3_min  NUMERIC,
    typical_density_kg_m3_max  NUMERIC,
    boiling_range_c_min        NUMERIC,
    boiling_range_c_max        NUMERIC,
    sort_order        INTEGER,
    source            TEXT NOT NULL DEFAULT 'IEA Energy Statistics Manual / JODI-Oil WD',
    attributes        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (parent_code IS NULL OR parent_code <> product_code),
    CHECK (category IS NULL OR category IN ('root', 'primary', 'secondary', 'aggregate'))
);

CREATE INDEX IF NOT EXISTS idx_oil_products_parent
    ON products.oil_products (parent_code);
CREATE INDEX IF NOT EXISTS idx_oil_products_jodi
    ON products.oil_products (jodi_code);
CREATE INDEX IF NOT EXISTS idx_oil_products_category
    ON products.oil_products (category);

COMMENT ON COLUMN products.oil_products.product_code IS
    'Stable slug used both as the local PK and as the proposed commodity code '
    'when this row is later promoted into oil_network.commodities.';
COMMENT ON COLUMN products.oil_products.category IS
    'root | primary (crude + refinery inputs) | secondary (refinery output) | '
    'aggregate (JODI-level rollup that has finer IEA children).';
COMMENT ON COLUMN products.oil_products.iea_code IS
    'Short tag used inside the IEA Oil Information statistical tables.';
COMMENT ON COLUMN products.oil_products.jodi_code IS
    'JODI-Oil World Database product code. NULL for IEA-only leaves that JODI '
    'does not split out (e.g. aviation_gasoline rolls up into GASOLINE).';
COMMENT ON COLUMN products.oil_products.attributes IS
    'Catch-all JSONB for unstructured per-product metadata: typical_uses, '
    'aliases, regulatory specs, notes, citations.';

-- Recursive descent view.
CREATE OR REPLACE VIEW products.v_oil_products_tree AS
WITH RECURSIVE t AS (
    SELECT
        product_code,
        parent_code,
        name,
        category,
        jodi_code,
        sort_order,
        1                                   AS depth,
        ARRAY[product_code]                 AS path,
        product_code                        AS root_code
    FROM products.oil_products
    WHERE parent_code IS NULL
    UNION ALL
    SELECT
        p.product_code,
        p.parent_code,
        p.name,
        p.category,
        p.jodi_code,
        p.sort_order,
        t.depth + 1,
        t.path || p.product_code,
        t.root_code
    FROM products.oil_products p
    JOIN t ON p.parent_code = t.product_code
)
SELECT * FROM t;

COMMENT ON VIEW products.v_oil_products_tree IS
    'Recursive descent of products.oil_products. One row per product with '
    'depth (root = 1), ancestor path array, and inherited root_code.';

-- JODI rollup view: which IEA-level products feed each JODI bucket.
CREATE OR REPLACE VIEW products.v_oil_products_jodi_rollup AS
WITH parents AS (
    SELECT product_code, jodi_code, name
    FROM products.oil_products
    WHERE jodi_code IS NOT NULL AND category = 'aggregate'
)
SELECT
    p.jodi_code,
    p.product_code   AS jodi_product_code,
    p.name           AS jodi_product_name,
    c.product_code   AS iea_product_code,
    c.name           AS iea_product_name
FROM parents p
LEFT JOIN products.oil_products c ON c.parent_code = p.product_code
ORDER BY p.jodi_code, c.product_code;

COMMENT ON VIEW products.v_oil_products_jodi_rollup IS
    'For each JODI aggregate product (GASOLINE, KEROJET, OTHPRODS, ...), '
    'lists the IEA-level children that roll up into it. Useful when mapping '
    'observed JODI time series onto the finer IEA leaves.';
"""


# ---------------------------------------------------------------------------
# Taxonomy data
# ---------------------------------------------------------------------------
# (product_code, parent_code, name, description, category, iea_code, jodi_code,
#  state_of_matter, density_min, density_max, boil_min, boil_max, sort_order,
#  attributes_dict)
#
# Descriptions paraphrased from IEA Energy Statistics Manual (2005) and the
# JODI-Oil WD product glossary. Density and boiling ranges are indicative
# bands published in the same sources; treat as typical, not specification.
# ---------------------------------------------------------------------------

PRODUCTS: list[tuple] = [
    # =====================================================================
    # Root
    # =====================================================================
    ("oil", None, "Oil (root)",
     "Generic root covering all primary and secondary oil products as defined "
     "in the IEA Energy Statistics Manual.",
     "root", None, None, None,
     None, None, None, None, 0,
     {"notes": "Synthetic root; not a JODI / IEA category in itself."}),

    # =====================================================================
    # Primary oil (crude + refinery inputs)
    # =====================================================================
    ("primary_oil", "oil", "Primary oil",
     "Crude oil and other hydrocarbon inputs that enter the refining stage "
     "without having been refined themselves.",
     "primary", None, None, None,
     None, None, None, None, 10,
     {"notes": "Aggregate; IEA balance row 'Primary oil supply'."}),

    ("crude_oil", "primary_oil", "Crude oil",
     "Mineral oil of natural origin, comprising a mixture of hydrocarbons and "
     "associated impurities (e.g. sulphur). Exists in the liquid phase under "
     "normal surface temperature and pressure. Includes lease condensate "
     "recovered at the wellhead. IEA convention: density typically "
     "800-1000 kg/m^3 at 15 degC.",
     "primary", "CRUDE", "CRUDEOIL", "liquid",
     800, 1000, None, None, 11,
     {"includes": ["lease_condensate", "synthetic_crude_from_tar_sands_pre_upgrading"],
      "excludes": ["natural_gas_liquids", "refinery_feedstocks"],
      "thesis_link": "This is the commodity currently modelled end-to-end (see Section 3 of the thesis)."}),

    ("ngl", "primary_oil", "Natural gas liquids (NGL)",
     "Liquid or liquefied hydrocarbons recovered from natural gas in "
     "separation facilities or gas-processing plants. Comprises ethane, "
     "propane, butane, isobutane, pentanes-plus and condensate produced "
     "downstream of the wellhead at gas plants.",
     "primary", "NGL", "NGL", "liquid",
     None, None, None, None, 12,
     {"components": ["ethane", "propane", "n_butane", "isobutane", "pentanes_plus", "plant_condensate"],
      "aliases": ["NGPL (US EIA term)"]}),

    ("refinery_feedstocks", "primary_oil", "Refinery feedstocks",
     "Processed oils destined for further processing in refineries (e.g. "
     "straight-run fuel oil, vacuum gasoil, atmospheric residue, hydrocracker "
     "feedstocks). Excludes additives and finished products.",
     "primary", "FEEDSTK", "OTHCRUDE", "liquid",
     None, None, None, None, 13,
     {"notes": "Reported under JODI's catch-all OTHCRUDE bucket alongside additives and other hydrocarbons."}),

    ("additives_oxygenates", "primary_oil", "Additives and oxygenates",
     "Non-hydrocarbon compounds blended into petroleum products to modify "
     "their properties: alcohols (methanol, ethanol when used as gasoline "
     "blendstock), ethers (MTBE, ETBE, TAME), esters (biodiesel when blended) "
     "and chemical additives (octane boosters, lubricity improvers).",
     "primary", "ADD", "OTHCRUDE", "liquid",
     None, None, None, None, 14,
     {"examples": ["MTBE", "ETBE", "ethanol_blendstock", "biodiesel_blendstock"]}),

    ("other_hydrocarbons", "primary_oil", "Other hydrocarbons",
     "Synthetic crude oil from tar sands, oil shale, etc.; liquids from coal "
     "liquefaction; hydrogen used as a refinery feedstock; emulsified oils.",
     "primary", "OTHHC", "OTHCRUDE", "liquid",
     None, None, None, None, 15,
     {"examples": ["synthetic_crude_from_tar_sands_upgraded", "shale_oil", "ctl", "gtl"]}),

    # =====================================================================
    # Secondary oil (refined products)
    # =====================================================================
    ("secondary_oil", "oil", "Secondary oil (refined products)",
     "Products obtained from the refining of crude oil and other primary oil "
     "inputs. Reported under JODI as TOTPRODUCTS.",
     "secondary", None, "TOTPRODUCTS", None,
     None, None, None, None, 100,
     {"notes": "Aggregate; sum of all refinery outputs."}),

    ("refinery_gas", "secondary_oil", "Refinery gas",
     "Non-condensable gas consisting of a mixture of methane, ethane, "
     "propylene, butylene and other light hydrocarbons obtained during "
     "distillation and conversion processes. Used primarily as fuel inside "
     "the refinery.",
     "secondary", "REFGAS", "OTHPRODS", "gas",
     None, None, None, None, 110,
     {"primary_use": "internal refinery fuel"}),

    ("ethane", "secondary_oil", "Ethane",
     "A naturally gaseous straight-chain hydrocarbon (C2H6) extracted from "
     "natural gas and refinery gas streams. Almost entirely used as "
     "petrochemical feedstock (ethylene crackers).",
     "secondary", "ETH", "OTHPRODS", "gas",
     None, None, -88.6, -88.6, 115,
     {"chemical_formula": "C2H6", "primary_use": "ethylene cracker feedstock",
      "notes": "boiling_range_c is the boiling point at 1 atm (single value used for both bounds)."}),

    ("lpg", "secondary_oil", "Liquefied petroleum gas (LPG)",
     "Light paraffinic hydrocarbons derived from refinery, stabilisation and "
     "natural gas processing plants. Consists mainly of propane (C3H8) and "
     "butane (C4H10), or a mixture of the two. Normally liquefied under "
     "pressure for transport and storage.",
     "secondary", "LPG", "LPG", "liquid",
     500, 580, -42, -0.5, 120,
     {"components": ["propane", "n_butane", "isobutane"],
      "primary_uses": ["residential_cooking_heating", "petrochemical_feedstock", "auto_lpg"],
      "phase_note": "gas at STP; stored / shipped as liquid under pressure or refrigeration."}),

    ("naphtha", "secondary_oil", "Naphtha",
     "Light or medium oil distilling between roughly 30 degC and 210 degC. "
     "Mainly used as petrochemical feedstock (steam crackers, reforming) and "
     "as a gasoline blending component.",
     "secondary", "NAPH", "NAPHTHA", "liquid",
     680, 750, 30, 210, 130,
     {"primary_uses": ["petrochemical_feedstock", "gasoline_blendstock"],
      "subgrades": ["light_naphtha", "heavy_naphtha"]}),

    # ----- Gasoline aggregate -----
    ("gasoline", "secondary_oil", "Gasoline",
     "Aggregate covering aviation gasoline + motor gasoline as reported under "
     "JODI's GASOLINE bucket.",
     "aggregate", None, "GASOLINE", "liquid",
     710, 770, 30, 215, 140,
     {"notes": "JODI groups aviation + motor gasoline; IEA splits them out."}),

    ("aviation_gasoline", "gasoline", "Aviation gasoline",
     "Motor spirit prepared specifically for piston-engined aircraft, with an "
     "octane rating suited to the engine and a freezing point of -60 degC. "
     "Distillation range 30 degC to 180 degC.",
     "secondary", "AVGAS", None, "liquid",
     700, 770, 30, 180, 141,
     {"primary_use": "piston-engined aircraft", "freezing_point_c_max": -60,
      "common_grades": ["100LL", "100/130", "82UL"]}),

    ("motor_gasoline", "gasoline", "Motor gasoline",
     "Light hydrocarbon oil for use in spark-ignition internal combustion "
     "engines other than aircraft. Distilling between 35 degC and 215 degC, "
     "typically with octane (RON) 90-100. May contain additives, oxygenates "
     "and biofuel blendstocks (IEA convention: excludes the biofuel portion "
     "when reported as 'motor gasoline excluding biofuels').",
     "secondary", "MOGAS", None, "liquid",
     720, 775, 35, 215, 142,
     {"primary_use": "spark-ignition road vehicles",
      "common_grades": ["regular_87", "midgrade_89", "premium_91_93", "RBOB", "CBOB"],
      "biofuel_blend_excluded": True}),

    # ----- Jet & kerosene aggregate -----
    ("jet_kerosene", "secondary_oil", "Jet fuel and kerosene",
     "Aggregate covering gasoline-type jet fuel, kerosene-type jet fuel and "
     "other kerosene as reported under JODI's KEROJET bucket.",
     "aggregate", None, "KEROJET", "liquid",
     750, 845, 100, 300, 150,
     {"notes": "JODI groups all kerosenes; IEA splits into jet (avgas-type), jet (kero-type) and other kerosene."}),

    ("gasoline_type_jet_fuel", "jet_kerosene", "Gasoline-type jet fuel",
     "Light hydrocarbon oil used in aviation turbine power units, distilling "
     "between 100 degC and 250 degC. Obtained by blending kerosene and "
     "gasoline / naphtha. Largely superseded by kerosene-type jet fuel; still "
     "used in some military and high-altitude applications (Jet B, JP-4).",
     "secondary", "JETK1", None, "liquid",
     750, 800, 100, 250, 151,
     {"common_grades": ["Jet_B", "JP-4"],
      "primary_use": "military and cold-climate aviation"}),

    ("kerosene_type_jet_fuel", "jet_kerosene", "Kerosene-type jet fuel",
     "Medium distillate used in aviation turbine engines, conforming to the "
     "freezing point and flash point specifications of Jet A-1 / Jet A. "
     "Distillation range typically 150 degC to 290 degC.",
     "secondary", "JETK2", None, "liquid",
     775, 840, 150, 290, 152,
     {"common_grades": ["Jet_A", "Jet_A-1", "JP-5", "JP-8"],
      "primary_use": "commercial and military jet aviation",
      "spec_notes": {"freezing_point_c_max": -47, "flash_point_c_min": 38}}),

    ("other_kerosene", "jet_kerosene", "Other kerosene",
     "Refined petroleum distillate used in sectors other than aircraft "
     "transport: domestic heating, lamps, cooking, illuminating and solvent "
     "applications. Distillation range 150 degC to 300 degC.",
     "secondary", "OTHKERO", None, "liquid",
     775, 845, 150, 300, 153,
     {"primary_uses": ["domestic_heating", "lamps", "cooking", "solvent"]}),

    # ----- Gas/diesel -----
    ("gas_diesel_oil", "secondary_oil", "Gas/diesel oil",
     "Heavy distillate boiling between approximately 180 degC and 380 degC. "
     "Covers heating gasoil (domestic, commercial and industrial heating), "
     "automotive diesel fuel (compression-ignition engines), marine diesel "
     "and rail diesel. Reported by IEA as 'Gas/diesel oil (excluding "
     "biofuels)' when the biofuel blendstock is unbundled.",
     "secondary", "GO", "GASDIES", "liquid",
     820, 880, 180, 380, 160,
     {"subgrades": ["automotive_diesel_ULSD", "heating_oil_no2", "marine_gasoil_MGO", "rail_diesel"],
      "biofuel_blend_excluded": True,
      "sulphur_specs_notes": "ULSD <= 15 ppm S in US / 10 ppm in EU."}),

    # ----- Residual fuel oil -----
    ("fuel_oil", "secondary_oil", "Fuel oil (residual)",
     "Heavy oils that constitute the residue from crude oil distillation. "
     "High viscosity and density; require heating before they can be pumped. "
     "Used in marine bunkers (HSFO, VLSFO, MGO blend), industrial furnaces, "
     "and power generation. IMO 2020 sulphur cap brought VLSFO (0.5 wt% S) to "
     "dominance in marine.",
     "secondary", "RFO", "RESFUEL", "liquid",
     920, 1010, 350, None, 170,
     {"subgrades": ["HSFO_3.5pct", "LSFO_1.0pct", "VLSFO_0.5pct", "ULSFO_0.1pct"],
      "primary_uses": ["marine_bunkers", "industrial_furnaces", "power_generation"],
      "imo_2020_note": "Global sulphur cap of 0.5 wt% S in marine fuels in effect since 1 Jan 2020."}),

    # ----- Other products aggregate -----
    ("other_products", "secondary_oil", "Other oil products",
     "Aggregate JODI bucket (OTHPRODS) covering all refinery output not "
     "captured by the gasoline / jet-kero / gasoil / fuel oil / LPG / naphtha "
     "categories.",
     "aggregate", None, "OTHPRODS", None,
     None, None, None, None, 180,
     {"notes": "JODI catch-all. IEA breaks this out into the children below."}),

    ("white_spirit_sbp", "other_products", "White spirit and SBP",
     "Refined distillate intermediates between naphtha and kerosene. White "
     "spirit is a solvent (paint thinner, dry cleaning); SBP (Special Boiling "
     "Point industrial spirits) covers narrow-cut light distillates used as "
     "industrial solvents.",
     "secondary", "WSP", None, "liquid",
     760, 810, 130, 220, 181,
     {"primary_uses": ["paint_solvent", "dry_cleaning", "industrial_solvent"]}),

    ("lubricants", "other_products", "Lubricants",
     "Hydrocarbons produced from distillate or residue; used principally to "
     "reduce friction between bearing surfaces. Spans engine oils, hydraulic "
     "fluids, gear oils, greases, metalworking fluids.",
     "secondary", "LUBE", None, "liquid",
     850, 950, 300, 600, 182,
     {"primary_uses": ["engine_oil", "hydraulic_fluid", "gear_oil", "metalworking_fluid"],
      "form_factors": ["liquid_oil", "grease"]}),

    ("bitumen", "other_products", "Bitumen",
     "Solid, semi-solid or viscous hydrocarbon with a colloidal structure, "
     "brown to black, obtained as the residue from vacuum distillation of "
     "crude oil. Used principally for paving roads and for waterproofing.",
     "secondary", "BIT", None, "semi-solid",
     1000, 1100, None, None, 183,
     {"aliases": ["asphalt (US English)"],
      "primary_uses": ["road_paving", "waterproofing", "roofing"]}),

    ("paraffin_waxes", "other_products", "Paraffin waxes",
     "Saturated aliphatic hydrocarbons (C20-C40) obtained as a by-product of "
     "dewaxing lubricant base oils. Solid at ambient temperature, melting "
     "between 45 degC and 65 degC. Used in candles, packaging, cosmetics, "
     "rubber compounds.",
     "secondary", "WAX", None, "solid",
     880, 920, None, None, 184,
     {"melting_range_c": [45, 65],
      "primary_uses": ["candles", "packaging", "cosmetics", "rubber_extender"]}),

    ("petroleum_coke", "other_products", "Petroleum coke",
     "Black solid by-product obtained mainly from cracking and carbonising "
     "heavy hydrocarbon residues. Two grades: green (fuel) and calcined "
     "(anode-grade for the aluminium and steel industries).",
     "secondary", "PETCOKE", None, "solid",
     1300, 1700, None, None, 185,
     {"subgrades": ["green_coke", "calcined_coke", "needle_coke", "fuel_grade_coke"],
      "primary_uses": ["aluminium_anode", "steel_electrode", "industrial_fuel", "cement_kiln_fuel"]}),

    ("other_oil_products_misc", "other_products", "Other oil products (unspecified)",
     "Residual catch-all for refinery output that does not fall into any of "
     "the named IEA categories (e.g. sulphur recovered at the refinery, "
     "minor specialty products).",
     "secondary", "OTH", None, None,
     None, None, None, None, 186,
     {"examples": ["recovered_sulphur", "specialty_solvents", "carbon_black_feedstock"]}),

    # =====================================================================
    # Sub-grades (3rd level: industry-distinguished sub-types within each
    # IEA leaf). These sit below the IEA-level leaves above and are the
    # granularity at which actual physical product is bought/sold/stored.
    # Useful for downstream refinery-yield modelling and bunker / road-fuel
    # analytics. Optional layer — the IEA-level leaves above remain the
    # canonical reporting buckets.
    # =====================================================================

    # ---- LPG components ----
    ("propane", "lpg", "Propane (C3H8)",
     "Three-carbon paraffin liquefied under low pressure. Sold as commercial "
     "propane (HD-5 spec: >= 90 vol% propane, < 5% propylene) for residential "
     "heating and cooking, autogas (auto-LPG), and petrochemical feedstock "
     "(propane dehydrogenation to propylene).",
     "secondary", "C3", "LPG", "gas",
     493, 510, -42.1, -42.1, 121,
     {"chemical_formula": "C3H8", "spec": "HD-5 (>=90 vol% propane)",
      "boiling_point_c": -42.1,
      "primary_uses": ["residential_heating", "autogas", "PDH_petrochemical_feedstock"]}),

    ("n_butane", "lpg", "n-Butane (n-C4H10)",
     "Normal (straight-chain) butane. Used as gasoline blendstock for Reid "
     "Vapour Pressure (RVP) control, refinery feedstock for alkylation and "
     "isomerisation, and petrochemical feedstock to steam crackers.",
     "secondary", "NC4", "LPG", "gas",
     572, 585, -0.5, -0.5, 122,
     {"chemical_formula": "n-C4H10", "boiling_point_c": -0.5,
      "primary_uses": ["gasoline_blendstock_RVP_control", "alkylation_feed",
                        "steam_cracker_feed"]}),

    ("isobutane", "lpg", "Isobutane (i-C4H10)",
     "Branched-chain butane. Predominantly used as alkylation feedstock to "
     "produce high-octane alkylate (a premium gasoline blendstock); also a "
     "refrigerant (R-600a) in domestic refrigeration.",
     "secondary", "IC4", "LPG", "gas",
     557, 565, -11.7, -11.7, 123,
     {"chemical_formula": "i-C4H10", "boiling_point_c": -11.7,
      "primary_uses": ["alkylation_feed_for_alkylate", "refrigerant_R600a"]}),

    ("lpg_mix", "lpg", "LPG mix (propane-butane blend)",
     "Commercial propane/butane mixtures with seasonal composition (higher "
     "propane fraction in winter for vapour-pressure performance). Common in "
     "residential and industrial markets where pure propane is not specified.",
     "secondary", "LPGMIX", "LPG", "gas",
     510, 580, -42, -0.5, 124,
     {"seasonal_blend_note": "Higher propane fraction in winter."}),

    # ---- Motor gasoline grades ----
    ("mogas_regular", "motor_gasoline", "Motor gasoline - regular (87 AKI)",
     "US regular unleaded gasoline, anti-knock index (AKI) 87, RON approx 91. "
     "Most common pump grade in the US. Typically blended with 10 vol% "
     "ethanol (E10) at the terminal.",
     "secondary", "MOGAS87", None, "liquid",
     720, 770, 35, 215, 1421,
     {"octane_AKI": 87, "octane_RON_approx": 91, "typical_ethanol_blend_pct": 10}),

    ("mogas_midgrade", "motor_gasoline", "Motor gasoline - midgrade (89 AKI)",
     "US midgrade unleaded gasoline, AKI 89. Often a splash blend of regular "
     "and premium at the pump rather than a separately refined product.",
     "secondary", "MOGAS89", None, "liquid",
     720, 770, 35, 215, 1422,
     {"octane_AKI": 89, "production_note": "Often splash-blended at the pump."}),

    ("mogas_premium", "motor_gasoline", "Motor gasoline - premium (91-93 AKI)",
     "US premium unleaded gasoline, AKI 91-93. Required by some high-"
     "compression engines; voluntary upgrade otherwise.",
     "secondary", "MOGAS93", None, "liquid",
     720, 770, 35, 215, 1423,
     {"octane_AKI_range": [91, 93]}),

    ("rbob", "motor_gasoline", "Reformulated blendstock for oxygenate blending (RBOB)",
     "Reformulated gasoline blendstock intended to be blended with ~10 vol% "
     "ethanol downstream of the refinery to produce finished reformulated "
     "gasoline (RFG). Required in US ozone non-attainment areas (Northeast "
     "cities, California markets).",
     "secondary", "RBOB", None, "liquid",
     720, 770, 35, 215, 1424,
     {"oxygenate_required": "10 vol% ethanol blend",
      "regulated_markets": "Reformulated gasoline (RFG) zones under CAA Title I",
      "futures_market": "RBOB gasoline futures (NYMEX RB)"}),

    ("cbob", "motor_gasoline", "Conventional blendstock for oxygenate blending (CBOB)",
     "Conventional gasoline blendstock for blending with ethanol to produce "
     "finished conventional gasoline. Used outside the RFG zones.",
     "secondary", "CBOB", None, "liquid",
     720, 770, 35, 215, 1425,
     {"oxygenate_typical": "10 vol% ethanol (E10)",
      "markets": "Conventional gasoline markets (outside RFG zones)"}),

    # ---- Kerosene-type jet fuel grades ----
    ("jet_a", "kerosene_type_jet_fuel", "Jet A",
     "US domestic civil-aviation kerosene-type jet fuel. ASTM D1655. Freezing "
     "point spec <= -40 degC. Standard at most US airports.",
     "secondary", "JETA", None, "liquid",
     775, 840, 150, 290, 1521,
     {"spec": "ASTM_D1655", "freezing_point_c_max": -40, "flash_point_c_min": 38,
      "primary_use": "US domestic civil aviation"}),

    ("jet_a1", "kerosene_type_jet_fuel", "Jet A-1",
     "International civil-aviation kerosene-type jet fuel. Freezing point "
     "spec <= -47 degC (colder than Jet A) to support long-haul / polar "
     "routes. ASTM D1655 and DEF STAN 91-091.",
     "secondary", "JETA1", None, "liquid",
     775, 840, 150, 290, 1522,
     {"specs": ["ASTM_D1655", "DEF_STAN_91-091"],
      "freezing_point_c_max": -47, "flash_point_c_min": 38,
      "primary_use": "International civil aviation"}),

    ("jp_5", "kerosene_type_jet_fuel", "JP-5",
     "US Navy high-flash-point kerosene jet fuel; flash point >= 60 degC for "
     "safety aboard aircraft carriers. NATO F-44.",
     "secondary", "JP5", None, "liquid",
     788, 845, 156, 290, 1523,
     {"flash_point_c_min": 60, "nato_code": "F-44",
      "primary_use": "US Navy carrier and shipboard aviation"}),

    ("jp_8", "kerosene_type_jet_fuel", "JP-8",
     "US military kerosene jet fuel based on Jet A-1 with a mandatory "
     "additive package (corrosion inhibitor, fuel system icing inhibitor, "
     "static dissipator). NATO F-34. Standard fuel for most US Air Force "
     "platforms.",
     "secondary", "JP8", None, "liquid",
     775, 840, 150, 290, 1524,
     {"basis": "Jet A-1 + military additive package", "nato_code": "F-34",
      "primary_use": "US Air Force / NATO ground and air operations"}),

    ("ts_1", "kerosene_type_jet_fuel", "TS-1",
     "Russian / CIS kerosene jet fuel (GOST 10227). Lighter cut and lower "
     "flash point (>= 28 degC) than Jet A-1; historically used in cold "
     "climates where the lighter cut aids cold-start.",
     "secondary", "TS1", None, "liquid",
     775, 830, 150, 280, 1525,
     {"spec": "GOST_10227", "flash_point_c_min": 28,
      "primary_markets": "Russia, CIS, parts of Eastern Europe"}),

    # ---- Gas/diesel oil grades ----
    ("ulsd", "gas_diesel_oil", "Ultra-low-sulphur diesel (ULSD)",
     "Automotive diesel with sulphur <= 15 ppm (US, EPA on-highway) / <= 10 "
     "ppm (EU, EN 590). Mandated for on-road use in the US since 2006 to "
     "enable modern emissions-control systems (DPF, SCR, NOx adsorber).",
     "secondary", "ULSD", None, "liquid",
     820, 845, 180, 360, 1601,
     {"sulphur_ppm_max": {"US_on_road": 15, "EU_EN590": 10},
      "specs": ["ASTM_D975_S15", "EN_590"],
      "primary_use": "on-road heavy-duty and light-duty diesel vehicles"}),

    ("lsd_offroad", "gas_diesel_oil", "Off-road / low-sulphur diesel (LSD)",
     "Off-road / non-road diesel. US EPA Tier 4 non-road fuel = 15 ppm S; "
     "legacy off-road allows up to 500 ppm S. Dyed red in the US to indicate "
     "untaxed off-road use. Powers construction equipment, farm equipment, "
     "stationary engines.",
     "secondary", "LSD", None, "liquid",
     820, 860, 180, 380, 1602,
     {"sulphur_ppm_range": [15, 500], "us_dye_color": "red",
      "tax_status": "off-road (no federal highway tax)",
      "primary_uses": ["construction_equipment", "farm_equipment", "stationary_engines"]}),

    ("heating_oil_no1", "gas_diesel_oil", "Heating oil No. 1",
     "Light distillate heating oil, kerosene-like. Used in vaporising-type "
     "burners and outdoor fuel-oil tanks in cold climates where No. 2 would "
     "wax (gel point too high). Sometimes called 'stove oil'.",
     "secondary", "HO1", None, "liquid",
     800, 830, 150, 290, 1603,
     {"aliases": ["stove_oil"],
      "primary_use": "residential heating in cold climates / outdoor tanks"}),

    ("heating_oil_no2", "gas_diesel_oil", "Heating oil No. 2",
     "The most common residential/commercial heating oil in the US Northeast. "
     "Distillate cut close to ULSD but historically higher sulphur. Many US "
     "states now mandate <= 15 ppm S ('Ultra-Low-Sulphur Heating Oil', "
     "ULSHO).",
     "secondary", "HO2", None, "liquid",
     820, 860, 180, 360, 1604,
     {"primary_use": "residential / commercial space heating",
      "sulphur_trend": "ULSHO (<=15 ppm S) mandates in NY, NJ, MA, CT, etc.",
      "futures_market": "NY Harbor ULSD / heating oil (NYMEX HO)"}),

    ("marine_gasoil", "gas_diesel_oil", "Marine gasoil (MGO)",
     "Distillate-only marine fuel (ISO 8217 DMA / DMZ grades). Lighter and "
     "lower sulphur than residual marine fuels; used in Emission Control "
     "Areas (ECAs) and on smaller vessels that cannot handle residual fuel.",
     "secondary", "MGO", None, "liquid",
     820, 890, 180, 380, 1605,
     {"iso_8217_grades": ["DMA", "DMZ"],
      "primary_use": "ECA-compliant marine fuel, small commercial vessels"}),

    ("rail_diesel", "gas_diesel_oil", "Rail / locomotive diesel",
     "Diesel fuel used in railroad locomotives. In the US, EPA Tier 4 "
     "locomotive standards effectively require ULSD-grade (<= 15 ppm S) "
     "fuel; in practice most US rail diesel is the same product as on-road "
     "ULSD.",
     "secondary", "RAIL", None, "liquid",
     820, 845, 180, 360, 1606,
     {"primary_use": "freight and passenger rail diesel locomotives",
      "us_regulatory": "EPA Tier 4 locomotive emissions standards"}),

    # ---- Fuel oil grades (sulphur-cut bunker market) ----
    ("hsfo", "fuel_oil", "High-sulphur fuel oil (HSFO)",
     "Residual marine bunker fuel with sulphur up to 3.5 wt%. Pre-IMO-2020 "
     "global standard; post-2020 only usable on vessels fitted with exhaust "
     "gas scrubbers (open-loop, closed-loop, hybrid).",
     "secondary", "HSFO", None, "liquid",
     950, 1010, 350, None, 1701,
     {"sulphur_pct_max": 3.5, "iso_8217_grades": ["RMG", "RMK"],
      "post_imo_2020_use": "scrubber-equipped vessels only"}),

    ("lsfo_1pct", "fuel_oil", "Low-sulphur fuel oil 1.0% S",
     "Residual fuel oil with sulphur <= 1.0 wt%. Historical ECA limit "
     "(2010-2014) and still encountered as an intermediate-sulphur bunker "
     "grade and industrial fuel.",
     "secondary", "LSFO1", None, "liquid",
     940, 1000, 350, None, 1702,
     {"sulphur_pct_max": 1.0}),

    ("vlsfo", "fuel_oil", "Very-low-sulphur fuel oil (VLSFO)",
     "Residual marine fuel with sulphur <= 0.5 wt%. Dominant post-IMO-2020 "
     "global bunker grade. Often a residual/distillate blend to meet the "
     "0.5% sulphur spec while keeping viscosity workable.",
     "secondary", "VLSFO", None, "liquid",
     930, 1000, 320, None, 1703,
     {"sulphur_pct_max": 0.5, "imo_2020_global_cap": True,
      "blend_note": "Many VLSFOs are residual/distillate blends to meet 0.5% S."}),

    ("ulsfo", "fuel_oil", "Ultra-low-sulphur fuel oil (ULSFO)",
     "Residual marine fuel with sulphur <= 0.1 wt%. Required inside Emission "
     "Control Areas (ECAs: North Sea, Baltic, North American 200nm coast, US "
     "Caribbean) since 2015.",
     "secondary", "ULSFO", None, "liquid",
     900, 980, 320, None, 1704,
     {"sulphur_pct_max": 0.1,
      "primary_use": "ECA-compliant residual marine fuel (or distillate-heavy blend)"}),

    ("bunker_c", "fuel_oil", "Bunker C / No. 6 fuel oil",
     "Traditional heavy industrial bunker grade (US No. 6 fuel oil). Very "
     "high viscosity; requires preheating to ~50 degC for pumping and "
     "~100 degC for atomisation in burners. Used in older steam boilers, "
     "industrial furnaces, and legacy power generation.",
     "secondary", "BNKC", None, "liquid",
     950, 1010, 400, None, 1705,
     {"us_grade": "No. 6 fuel oil",
      "primary_uses": ["industrial_steam_boiler", "legacy_power_generation"]}),

    # ---- Bitumen grades ----
    ("paving_bitumen", "bitumen", "Paving bitumen",
     "Bitumen graded for asphalt road construction. Three concurrent grading "
     "systems are in use globally: penetration grade (e.g. 60/70, 80/100), "
     "viscosity grade (AC-20, AC-30), and Superpave performance grade "
     "(PG 64-22, PG 76-22). Performance grading is now dominant in the US.",
     "secondary", "PAVBIT", None, "semi-solid",
     1000, 1050, None, None, 1831,
     {"grading_systems": ["penetration", "viscosity", "performance_PG"],
      "examples": ["60/70", "80/100", "PG_64-22", "PG_76-22"],
      "primary_use": "asphalt road and airfield paving"}),

    ("industrial_bitumen", "bitumen", "Industrial bitumen (oxidised)",
     "Bitumen blown with air at elevated temperature to increase softening "
     "point and reduce penetration. Used for roofing felts, waterproofing "
     "membranes, and pipe-coating compounds.",
     "secondary", "INDBIT", None, "semi-solid",
     1000, 1100, None, None, 1832,
     {"process": "air-blowing / oxidation",
      "primary_uses": ["roofing_felt", "waterproofing_membrane", "pipe_coating"]}),

    ("polymer_modified_bitumen", "bitumen", "Polymer-modified bitumen (PMB)",
     "Bitumen blended with elastomeric or plastomeric polymers (SBS, SBR, "
     "EVA, atactic polypropylene) to improve rutting resistance and low-"
     "temperature performance. Premium binder for high-traffic and extreme-"
     "climate paving.",
     "secondary", "PMB", None, "semi-solid",
     1000, 1050, None, None, 1833,
     {"modifiers": ["SBS", "SBR", "EVA", "APP"],
      "primary_uses": ["high_performance_paving", "stress_absorbing_membrane"]}),

    ("cutback_bitumen", "bitumen", "Cutback bitumen",
     "Bitumen diluted with a petroleum solvent (kerosene, gasoline or diesel) "
     "to reduce viscosity for cold application. Classified by cure speed: "
     "rapid-curing (RC), medium-curing (MC), slow-curing (SC). Less used "
     "today due to VOC emissions; emulsions preferred.",
     "secondary", "CUTBIT", None, "liquid",
     940, 1010, None, None, 1834,
     {"cure_classes": ["RC", "MC", "SC"],
      "solvents": ["kerosene", "gasoline", "diesel"]}),

    ("bitumen_emulsion", "bitumen", "Bitumen emulsion",
     "Stable dispersion of bitumen droplets in water with an emulsifier "
     "(cationic or anionic). Used cold for chip seals, slurry seals, tack "
     "coats. Breaks on contact with the substrate to deposit residual "
     "bitumen.",
     "secondary", "EMULBIT", None, "liquid",
     1010, 1030, None, None, 1835,
     {"charge_types": ["cationic", "anionic"],
      "primary_uses": ["chip_seal", "slurry_seal", "tack_coat", "cold_mix"]}),

    # ---- Lubricant categories ----
    ("engine_oil", "lubricants", "Engine oil",
     "Lubricating oils formulated for internal combustion engines. Covers "
     "passenger-car motor oil (PCMO) and heavy-duty engine oil (HDEO). "
     "Current PCMO categories: API SP / ILSAC GF-6A; current HDEO: API CK-4 "
     "/ FA-4. SAE viscosity grades (e.g. 0W-20, 5W-30, 15W-40).",
     "secondary", "EO", None, "liquid",
     840, 900, 300, 550, 1821,
     {"subcategories": ["PCMO", "HDEO"],
      "current_api_categories": ["API_SP", "ILSAC_GF-6A", "API_CK-4", "API_FA-4"],
      "viscosity_grades_example": ["0W-20", "5W-30", "10W-30", "15W-40"]}),

    ("industrial_lubricant", "lubricants", "Industrial lubricant",
     "Lubricating fluids for industrial machinery: hydraulic oils, gear oils, "
     "turbine oils, compressor oils, metalworking fluids, transformer / "
     "electrical insulating oils. ISO VG viscosity grades.",
     "secondary", "INDLUB", None, "liquid",
     850, 920, 300, 600, 1822,
     {"categories": ["hydraulic", "gear", "turbine", "compressor",
                      "metalworking", "transformer_dielectric"],
      "viscosity_system": "ISO_VG"}),

    ("base_oil", "lubricants", "Base oil",
     "Refined lubricant base fluid before the performance additive package "
     "is blended in. API base-stock groups: Group I (solvent-refined), "
     "II (hydrocracked), III (severely hydrocracked, very high VI), "
     "IV (PAO synthetic), V (esters, naphthenics, other).",
     "secondary", "BASEOIL", None, "liquid",
     840, 900, 300, 550, 1823,
     {"api_groups": {"I": "solvent_refined", "II": "hydrocracked",
                      "III": "severely_hydrocracked_VHVI",
                      "IV": "PAO_synthetic",
                      "V": "esters_naphthenics_other"}}),

    ("grease", "lubricants", "Grease",
     "Semi-solid lubricant consisting of a base oil thickened with a soap "
     "(lithium, calcium, sodium, complex lithium) or polyurea thickener. "
     "Consistency rated on the NLGI scale 000 (fluid) to 6 (very firm); "
     "NLGI 2 is the most common.",
     "secondary", "GREASE", None, "semi-solid",
     880, 950, None, None, 1824,
     {"thickeners": ["lithium_soap", "calcium_soap", "sodium_soap",
                      "complex_lithium", "polyurea"],
      "consistency_scale": "NLGI_000_to_6"}),

    # ---- Petroleum coke grades ----
    ("green_coke", "petroleum_coke", "Green petroleum coke (fuel-grade)",
     "Raw delayed-coker product. High sulphur and metals content. Used as "
     "fuel in cement kilns, power generation, and industrial boilers; or as "
     "feedstock to calciners.",
     "secondary", "GPC", None, "solid",
     1300, 1500, None, None, 1851,
     {"primary_uses": ["cement_kiln_fuel", "power_generation",
                        "calciner_feedstock", "industrial_boiler"],
      "typical_sulphur_pct_range": [3, 8]}),

    ("calcined_coke", "petroleum_coke", "Calcined petroleum coke (anode-grade)",
     "Green coke calcined at ~1300 degC to drive off volatiles and increase "
     "carbon content / density. Used to make pre-baked anodes for aluminium "
     "smelting (Hall-Heroult process). ~0.4 t calcined coke per tonne Al.",
     "secondary", "CPC", None, "solid",
     1900, 2100, None, None, 1852,
     {"process_temp_c": 1300,
      "primary_use": "pre-baked anode for aluminium smelting",
      "typical_sulphur_pct_range": [1, 3]}),

    ("needle_coke", "petroleum_coke", "Needle petroleum coke",
     "Premium-grade coke with highly anisotropic 'needle' microstructure. "
     "Made from low-sulphur, low-metals feedstocks (decant oil, FCC slurry "
     "oil). Used to make graphite electrodes for electric-arc furnaces in "
     "steelmaking, and as precursor for Li-ion battery anode material.",
     "secondary", "NPC", None, "solid",
     1700, 1900, None, None, 1853,
     {"primary_uses": ["graphite_electrode_EAF_steel", "Li-ion_anode_precursor"],
      "feedstock_constraints": "very low S and metals required"}),
]


UPSERT_SQL = """
INSERT INTO products.oil_products
    (product_code, parent_code, name, description, category,
     iea_code, jodi_code, state_of_matter,
     typical_density_kg_m3_min, typical_density_kg_m3_max,
     boiling_range_c_min, boiling_range_c_max,
     sort_order, attributes)
VALUES %s
ON CONFLICT (product_code) DO UPDATE SET
    parent_code               = EXCLUDED.parent_code,
    name                      = EXCLUDED.name,
    description               = EXCLUDED.description,
    category                  = EXCLUDED.category,
    iea_code                  = EXCLUDED.iea_code,
    jodi_code                 = EXCLUDED.jodi_code,
    state_of_matter           = EXCLUDED.state_of_matter,
    typical_density_kg_m3_min = EXCLUDED.typical_density_kg_m3_min,
    typical_density_kg_m3_max = EXCLUDED.typical_density_kg_m3_max,
    boiling_range_c_min       = EXCLUDED.boiling_range_c_min,
    boiling_range_c_max       = EXCLUDED.boiling_range_c_max,
    sort_order                = EXCLUDED.sort_order,
    attributes                = EXCLUDED.attributes;
"""


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("[1] schema DDL (CREATE SCHEMA + TABLE + VIEWS)")
        cur.execute(SCHEMA_DDL)

        print(f"[2] upsert {len(PRODUCTS)} oil-product rows")
        rows = [
            (
                code, parent, name, desc, cat, iea, jodi, phase,
                d_min, d_max, b_min, b_max, sort_order,
                json.dumps(attrs),
            )
            for (code, parent, name, desc, cat, iea, jodi, phase,
                 d_min, d_max, b_min, b_max, sort_order, attrs) in PRODUCTS
        ]
        execute_values(cur, UPSERT_SQL, rows, page_size=200)

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM products.oil_products")
        n_total = cur.fetchone()[0]
        cur.execute("SELECT category, COUNT(*) FROM products.oil_products "
                    "GROUP BY category ORDER BY category NULLS LAST")
        cats = cur.fetchall()

        print(f"\nFinal state: {n_total} rows in products.oil_products")
        print("  by category:")
        for cat, n in cats:
            print(f"    {cat or '(none)':<10}  {n}")

        print("\nTree (depth-first from 'oil'):")
        cur.execute("""
            SELECT depth, product_code, name, jodi_code
            FROM products.v_oil_products_tree
            ORDER BY path
        """)
        for depth, code, name, jodi in cur.fetchall():
            indent = "  " * (depth - 1)
            jodi_tag = f"  [JODI: {jodi}]" if jodi else ""
            print(f"  {indent}{code}  -- {name}{jodi_tag}")


if __name__ == "__main__":
    main()
