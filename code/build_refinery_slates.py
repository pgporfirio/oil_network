"""Algorithmic baseline generator for refinery x crude-grade yield slates.

For each (refinery, grade) row in refineries.refinery_grade_assignments, this
produces a leaf-level product yield slate (volume %) using:

  1. A per-grade **crude cut profile** (lpg / light_naphtha / heavy_naphtha /
     kerosene / atmos_gasoil / vacuum_gasoil / vacuum_resid), driven by the
     grade's API and density class from oil_network.commodities.

  2. A per-refinery **archetype transform** that converts each cut into main
     product groups. Archetype is hydroskimming / cracking / coking /
     deep_conversion, derived from NCI + has_coker + has_hydrocracker.

  3. **Plant-type overrides** for asphalt plants, condensate splitters,
     topping plants, lubricant specialty refineries — these short-circuit
     the archetype with a fixed slate template.

  4. A per-PADD **leaf allocation** for each main product group (gasoline ->
     mogas_regular / midgrade / premium / rbob / cbob with PADD-aware mix;
     diesel -> ulsd dominant; fuel_oil -> VLSFO/HSFO/etc; etc.).

Output: writes config/refinery_grade_slates_baseline.json with one entry per
(refinery, grade) pair, each carrying a `grades`-style array of
(product_code, yield_pct, source='algorithmic_baseline', notes).

This file is the audit trail for the algorithmic step. Web overrides
collected later replace specific (refinery, grade, product) rows in a
separate file; the merge step combines both into the final seed loaded by
load_refinery_slates.py.

Designed to be re-runnable: writes a deterministic JSON given the live DB
state. Re-running overwrites the output file in place.
"""
from __future__ import annotations

import json
from datetime import date

import psycopg2

from paths import CONFIG_DIR

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
OUT = CONFIG_DIR / "refinery_grade_slates_baseline.json"


# ---------------------------------------------------------------------------
# Crude cut profiles (volume %, sum ~100)
#
# Light/Medium/Heavy cuts derived from typical assay tables. For grades not
# explicitly listed, we fall back to a profile interpolated from the
# (sweet_sour, density_class) classification.
# ---------------------------------------------------------------------------

CUT_PROFILES_BY_GRADE: dict[str, dict[str, float]] = {
    # ---- Sweet light (38-44 API, ~0.2-0.5% S) ----
    "wti":              {"lpg": 2.0, "light_naphtha": 12.0, "heavy_naphtha": 18.0,
                         "kerosene": 12.0, "atmos_gasoil": 25.0, "vacuum_gasoil": 22.0,
                         "vacuum_resid": 9.0},
    "wti_midland":      {"lpg": 2.5, "light_naphtha": 14.0, "heavy_naphtha": 20.0,
                         "kerosene": 12.0, "atmos_gasoil": 24.0, "vacuum_gasoil": 20.0,
                         "vacuum_resid": 7.5},
    "wti_cushing":      {"lpg": 2.0, "light_naphtha": 12.0, "heavy_naphtha": 18.0,
                         "kerosene": 12.0, "atmos_gasoil": 25.0, "vacuum_gasoil": 22.0,
                         "vacuum_resid": 9.0},
    "wti_houston":      {"lpg": 2.0, "light_naphtha": 12.0, "heavy_naphtha": 18.0,
                         "kerosene": 12.0, "atmos_gasoil": 25.0, "vacuum_gasoil": 22.0,
                         "vacuum_resid": 9.0},
    "bakken_light":     {"lpg": 3.0, "light_naphtha": 14.0, "heavy_naphtha": 20.0,
                         "kerosene": 12.0, "atmos_gasoil": 24.0, "vacuum_gasoil": 19.0,
                         "vacuum_resid": 8.0},
    "eagle_ford_light": {"lpg": 3.0, "light_naphtha": 15.0, "heavy_naphtha": 21.0,
                         "kerosene": 13.0, "atmos_gasoil": 22.0, "vacuum_gasoil": 18.0,
                         "vacuum_resid": 8.0},
    "lls":              {"lpg": 2.0, "light_naphtha": 10.0, "heavy_naphtha": 16.0,
                         "kerosene": 11.0, "atmos_gasoil": 26.0, "vacuum_gasoil": 24.0,
                         "vacuum_resid": 11.0},
    "niobrara_sweet":   {"lpg": 2.5, "light_naphtha": 12.0, "heavy_naphtha": 18.0,
                         "kerosene": 12.0, "atmos_gasoil": 25.0, "vacuum_gasoil": 22.0,
                         "vacuum_resid": 8.5},
    "oklahoma_sweet":   {"lpg": 2.0, "light_naphtha": 11.0, "heavy_naphtha": 17.0,
                         "kerosene": 12.0, "atmos_gasoil": 26.0, "vacuum_gasoil": 23.0,
                         "vacuum_resid": 9.0},
    "wyoming_sweet":    {"lpg": 2.0, "light_naphtha": 10.0, "heavy_naphtha": 17.0,
                         "kerosene": 12.0, "atmos_gasoil": 25.0, "vacuum_gasoil": 24.0,
                         "vacuum_resid": 10.0},

    # ---- Very light / condensate ----
    "eagle_ford_condensate": {"lpg": 5.0, "light_naphtha": 25.0, "heavy_naphtha": 30.0,
                              "kerosene": 14.0, "atmos_gasoil": 18.0, "vacuum_gasoil": 7.0,
                              "vacuum_resid": 1.0},
    "permian_condensate":    {"lpg": 5.0, "light_naphtha": 25.0, "heavy_naphtha": 30.0,
                              "kerosene": 14.0, "atmos_gasoil": 18.0, "vacuum_gasoil": 7.0,
                              "vacuum_resid": 1.0},
    "wtl":              {"lpg": 4.0, "light_naphtha": 22.0, "heavy_naphtha": 26.0,
                         "kerosene": 13.0, "atmos_gasoil": 19.0, "vacuum_gasoil": 12.0,
                         "vacuum_resid": 4.0},

    # ---- Medium sour (28-33 API, ~0.8-2.0% S) ----
    "mars":             {"lpg": 1.5, "light_naphtha": 8.0, "heavy_naphtha": 13.0,
                         "kerosene": 10.0, "atmos_gasoil": 24.0, "vacuum_gasoil": 28.0,
                         "vacuum_resid": 15.5},
    "poseidon":         {"lpg": 1.5, "light_naphtha": 8.0, "heavy_naphtha": 13.0,
                         "kerosene": 10.0, "atmos_gasoil": 24.0, "vacuum_gasoil": 28.0,
                         "vacuum_resid": 15.5},
    "thunder_horse":    {"lpg": 1.5, "light_naphtha": 9.0, "heavy_naphtha": 14.0,
                         "kerosene": 11.0, "atmos_gasoil": 25.0, "vacuum_gasoil": 27.0,
                         "vacuum_resid": 12.5},
    "southern_green_canyon": {"lpg": 1.0, "light_naphtha": 7.0, "heavy_naphtha": 12.0,
                              "kerosene": 10.0, "atmos_gasoil": 24.0, "vacuum_gasoil": 28.0,
                              "vacuum_resid": 18.0},
    "ans":              {"lpg": 1.5, "light_naphtha": 8.0, "heavy_naphtha": 13.0,
                         "kerosene": 11.0, "atmos_gasoil": 25.0, "vacuum_gasoil": 27.0,
                         "vacuum_resid": 14.5},

    # ---- Heavy sour (12-25 API, ~0.8-3.0% S) ----
    "california_heavy": {"lpg": 1.0, "light_naphtha": 4.0, "heavy_naphtha": 8.0,
                         "kerosene": 6.0, "atmos_gasoil": 18.0, "vacuum_gasoil": 30.0,
                         "vacuum_resid": 33.0},
    "kern_heavy":       {"lpg": 0.5, "light_naphtha": 3.0, "heavy_naphtha": 7.0,
                         "kerosene": 5.0, "atmos_gasoil": 17.0, "vacuum_gasoil": 30.0,
                         "vacuum_resid": 37.5},
    "midway_sunset":    {"lpg": 0.5, "light_naphtha": 3.0, "heavy_naphtha": 6.0,
                         "kerosene": 5.0, "atmos_gasoil": 16.0, "vacuum_gasoil": 30.0,
                         "vacuum_resid": 39.5},
    "wyoming_asphaltic":{"lpg": 0.5, "light_naphtha": 4.0, "heavy_naphtha": 8.0,
                         "kerosene": 6.0, "atmos_gasoil": 18.0, "vacuum_gasoil": 28.0,
                         "vacuum_resid": 35.5},

    # ---- Generic root: medium representative profile ----
    "crude":            {"lpg": 2.0, "light_naphtha": 10.0, "heavy_naphtha": 16.0,
                         "kerosene": 11.0, "atmos_gasoil": 24.0, "vacuum_gasoil": 25.0,
                         "vacuum_resid": 12.0},
}


def fallback_cut_profile(sweet_sour: str | None, density_class: str | None) -> dict[str, float]:
    """When a grade isn't explicitly in CUT_PROFILES_BY_GRADE."""
    if density_class == "very_light":
        return CUT_PROFILES_BY_GRADE["eagle_ford_condensate"]
    if density_class == "heavy":
        return CUT_PROFILES_BY_GRADE["california_heavy"]
    if sweet_sour in ("medium_sour", "sour"):
        return CUT_PROFILES_BY_GRADE["mars"]
    return CUT_PROFILES_BY_GRADE["wti"]


# ---------------------------------------------------------------------------
# Archetype transformation matrix
#
# Each cut becomes some mix of main product groups; fractions sum to ~1
# per cut. The archetype is selected per-refinery from NCI + process flags.
# Main-group keys used here MUST exist in products.oil_products.
# ---------------------------------------------------------------------------

ARCHETYPE_TRANSFORMS: dict[str, dict[str, dict[str, float]]] = {
    "hydroskimming": {
        "lpg":           {"lpg": 1.00},
        "light_naphtha": {"motor_gasoline": 0.80, "naphtha": 0.20},
        "heavy_naphtha": {"motor_gasoline": 0.95, "refinery_gas": 0.05},
        "kerosene":      {"kerosene_type_jet_fuel": 1.00},
        "atmos_gasoil":  {"gas_diesel_oil": 0.93, "refinery_gas": 0.07},
        "vacuum_gasoil": {"fuel_oil": 0.92, "refinery_gas": 0.08},
        "vacuum_resid":  {"fuel_oil": 0.97, "refinery_gas": 0.03},
    },
    "cracking": {  # FCC and/or hydrocracker
        # Pass-2 calibration (2026-05-20 night): LPG slip across light/heavy
        # naphtha (reformer + debutanizer) and VGO (FCC C3/C4) bumped to
        # match IEA / EIA US LPG yield ~5-7% — earlier slip only counted
        # straight-run LPG and gave ~4%. Diesel slice off vacuum_resid
        # also shaved (0.18 -> 0.14) since US cracking refineries blend a
        # larger share of resid down to HSFO than the IEA Aug-2024 typical
        # implies. Calibrated against IEA Refining Margin Methodology +
        # EIA PADD-level product yields.
        "lpg":           {"lpg": 1.00},
        "light_naphtha": {"motor_gasoline": 0.76, "naphtha": 0.18, "lpg": 0.06},
        "heavy_naphtha": {"motor_gasoline": 0.90, "lpg": 0.05, "refinery_gas": 0.05},
        "kerosene":      {"kerosene_type_jet_fuel": 0.98, "gas_diesel_oil": 0.02},
        "atmos_gasoil":  {"gas_diesel_oil": 0.93, "lpg": 0.02, "refinery_gas": 0.05},
        "vacuum_gasoil": {"motor_gasoline": 0.60, "gas_diesel_oil": 0.20,
                          "lpg": 0.08, "fuel_oil": 0.06, "refinery_gas": 0.06},
        "vacuum_resid":  {"fuel_oil": 0.62, "gas_diesel_oil": 0.14,
                          "motor_gasoline": 0.13, "lpg": 0.06, "refinery_gas": 0.05},
    },
    "coking": {  # FCC + delayed coker, no hydrocracker
        # Pass-2 calibration: same LPG slip bumps as cracking; coker
        # vacuum_resid petcoke fraction dropped 0.28 -> 0.25 to bring
        # medium-sour grades from 4-5% petcoke into the IEA <=4% range
        # for coking refineries (the saved volume goes to LPG + diesel).
        "lpg":           {"lpg": 1.00},
        "light_naphtha": {"motor_gasoline": 0.76, "naphtha": 0.18, "lpg": 0.06},
        "heavy_naphtha": {"motor_gasoline": 0.90, "lpg": 0.05, "refinery_gas": 0.05},
        "kerosene":      {"kerosene_type_jet_fuel": 1.00},
        "atmos_gasoil":  {"gas_diesel_oil": 0.93, "lpg": 0.02, "refinery_gas": 0.05},
        "vacuum_gasoil": {"motor_gasoline": 0.55, "gas_diesel_oil": 0.20,
                          "lpg": 0.08, "fuel_oil": 0.10, "refinery_gas": 0.07},
        "vacuum_resid":  {"petroleum_coke": 0.25, "gas_diesel_oil": 0.27,
                          "motor_gasoline": 0.20, "lpg": 0.10, "fuel_oil": 0.12,
                          "refinery_gas": 0.06},
    },
    "deep_conversion": {  # FCC + coker + hydrocracker (max distillate)
        # Pass-2 calibration: petcoke 0.25 -> 0.22, LPG bumps; remaining
        # mass goes to LPG.
        "lpg":           {"lpg": 1.00},
        "light_naphtha": {"motor_gasoline": 0.76, "naphtha": 0.18, "lpg": 0.06},
        "heavy_naphtha": {"motor_gasoline": 0.90, "lpg": 0.05, "refinery_gas": 0.05},
        "kerosene":      {"kerosene_type_jet_fuel": 1.00},
        "atmos_gasoil":  {"gas_diesel_oil": 0.94, "lpg": 0.02, "refinery_gas": 0.04},
        "vacuum_gasoil": {"gas_diesel_oil": 0.48, "motor_gasoline": 0.30,
                          "lpg": 0.08, "fuel_oil": 0.08, "refinery_gas": 0.06},
        "vacuum_resid":  {"petroleum_coke": 0.22, "gas_diesel_oil": 0.32,
                          "motor_gasoline": 0.25, "lpg": 0.10, "fuel_oil": 0.05,
                          "refinery_gas": 0.06},
    },
}


def determine_archetype(nci: float | None, has_coker: bool | None,
                        has_hydrocracker: bool | None) -> str:
    if has_coker and (has_hydrocracker or (nci is not None and nci >= 13)):
        return "deep_conversion"
    if has_coker:
        return "coking"
    if has_hydrocracker or (nci is not None and nci >= 6):
        return "cracking"
    # Pure hydroskimming requires explicit evidence: NCI is set and <5.
    # Otherwise default to cracking — 90%+ of US refining capacity is at
    # least cracking-complexity (NCI 5+). Defaulting unknown-config
    # refineries to hydroskimming under-cracked them and inflated their
    # fuel_oil yield by ~15 pct against benchmark.
    if nci is not None and nci < 5:
        return "hydroskimming"
    return "cracking"


# ---------------------------------------------------------------------------
# Plant-type overrides
#
# Specialty plants don't run the standard archetype model. Their slate
# is mostly determined by what they're built to make.
# ---------------------------------------------------------------------------

PLANT_TYPE_TEMPLATES: dict[str, dict[str, float]] = {
    "asphalt_plant": {
        "bitumen":        60.0,
        "naphtha":        12.0,
        "gas_diesel_oil": 10.0,
        "fuel_oil":       10.0,
        "motor_gasoline":  3.0,
        "refinery_gas":    5.0,
    },
    "condensate_splitter": {
        "naphtha":               35.0,
        "motor_gasoline":        25.0,
        "kerosene_type_jet_fuel":15.0,
        "gas_diesel_oil":        15.0,
        "lpg":                    5.0,
        "refinery_gas":           5.0,
    },
    "topping_plant": {  # CDU only, no conversion (e.g. ANS for Alaska local use)
        "gas_diesel_oil":        28.0,
        "fuel_oil":              35.0,
        "kerosene_type_jet_fuel":12.0,
        "motor_gasoline":        15.0,
        "lpg":                    3.0,
        "naphtha":                5.0,
        "refinery_gas":           2.0,
    },
    "lubricant_plant": {
        "lubricants":     35.0,
        "gas_diesel_oil": 25.0,
        "motor_gasoline": 15.0,
        "fuel_oil":       12.0,
        "naphtha":         5.0,
        "kerosene_type_jet_fuel": 3.0,
        "refinery_gas":    5.0,
    },
}


def detect_plant_type(refinery: dict) -> str | None:
    name = (refinery.get("name") or "").lower()
    operator = (refinery.get("operator") or "").lower()
    site = (refinery.get("site") or "").lower()

    if "asphalt" in name or "paving" in name or "bitumen" in name:
        return "asphalt_plant"
    if "condensate" in name or "splitter" in name or "processing" in name:
        return "condensate_splitter"
    # Petro Star and Hilcorp Prudhoe Bay are pure ANS topping plants;
    # ConocoPhillips Prudhoe Bay is the same.
    operator_topping = any(
        s in operator for s in ("petro star", "hilcorp", "conocophillips alaska"))
    if operator_topping or "topping" in name:
        return "topping_plant"
    # Calumet / Ergon / Cross / American Bradford = specialty lubricant/wax
    operator_lube = any(
        s in operator for s in ("calumet", "ergon", "cross oil", "american refining group"))
    if operator_lube or "lubricant" in name:
        return "lubricant_plant"
    return None


# ---------------------------------------------------------------------------
# Leaf-product allocation
#
# Each main-product group splits into leaf products with PADD-aware shares.
# Aggregation parents in products.oil_products are: gasoline (aggregate),
# motor_gasoline (parent of mogas/RBOB/CBOB), jet_kerosene (aggregate),
# kerosene_type_jet_fuel (parent of jet_a etc.), gas_diesel_oil (parent
# of ULSD/etc.), fuel_oil (parent of VLSFO/etc.), lpg (parent of propane/etc.),
# bitumen (parent of paving/industrial etc.), lubricants (parent of engine/
# base/etc.), petroleum_coke (parent of green/calcined/etc.). The output
# always uses leaf product codes — never aggregates.
# ---------------------------------------------------------------------------

# Main-group -> dict[PADD or '*', dict[leaf_product_code, share]]
LEAF_ALLOCATIONS: dict[str, dict[str, dict[str, float]]] = {
    "motor_gasoline": {
        "PADD1": {"rbob": 0.40, "mogas_regular": 0.30, "mogas_midgrade": 0.10, "mogas_premium": 0.20},
        "PADD2": {"cbob": 0.30, "mogas_regular": 0.40, "mogas_midgrade": 0.10, "mogas_premium": 0.20},
        "PADD3": {"cbob": 0.25, "mogas_regular": 0.45, "mogas_midgrade": 0.10, "mogas_premium": 0.20},
        "PADD4": {"mogas_regular": 0.50, "mogas_midgrade": 0.10, "mogas_premium": 0.20, "cbob": 0.20},
        "PADD5": {"rbob": 0.35, "mogas_regular": 0.25, "mogas_midgrade": 0.10, "mogas_premium": 0.30},
        "*":     {"mogas_regular": 0.45, "mogas_midgrade": 0.10, "mogas_premium": 0.20, "cbob": 0.25},
    },
    "kerosene_type_jet_fuel": {
        "*": {"jet_a": 0.88, "jet_a1": 0.05, "jp_8": 0.04, "other_kerosene": 0.03},
    },
    "gas_diesel_oil": {
        "PADD1": {"ulsd": 0.78, "heating_oil_no2": 0.16, "marine_gasoil": 0.04, "rail_diesel": 0.02},
        "PADD2": {"ulsd": 0.88, "heating_oil_no2": 0.05, "marine_gasoil": 0.03, "rail_diesel": 0.04},
        "PADD3": {"ulsd": 0.86, "heating_oil_no2": 0.02, "marine_gasoil": 0.07, "rail_diesel": 0.05},
        "PADD4": {"ulsd": 0.92, "heating_oil_no2": 0.02, "rail_diesel": 0.06},
        "PADD5": {"ulsd": 0.90, "marine_gasoil": 0.07, "rail_diesel": 0.03},
        "*":     {"ulsd": 0.86, "heating_oil_no2": 0.06, "marine_gasoil": 0.04, "rail_diesel": 0.04},
    },
    "fuel_oil": {
        "*": {"vlsfo": 0.45, "hsfo": 0.20, "lsfo_1pct": 0.10, "ulsfo": 0.15, "bunker_c": 0.10},
    },
    "lpg": {
        "*": {"propane": 0.50, "n_butane": 0.25, "isobutane": 0.20, "lpg_mix": 0.05},
    },
    "petroleum_coke": {
        "*": {"green_coke": 0.92, "calcined_coke": 0.07, "needle_coke": 0.01},
    },
    "bitumen": {
        "*": {"paving_bitumen": 0.78, "industrial_bitumen": 0.10,
              "polymer_modified_bitumen": 0.06, "cutback_bitumen": 0.04,
              "bitumen_emulsion": 0.02},
    },
    "lubricants": {
        "*": {"base_oil": 0.40, "engine_oil": 0.30, "industrial_lubricant": 0.25, "grease": 0.05},
    },
    # Single-leaf groups (no further split): naphtha, refinery_gas, etc.
    "naphtha":      {"*": {"naphtha": 1.00}},
    "refinery_gas": {"*": {"refinery_gas": 1.00}},
}


def allocate_leaf(main_group: str, padd: str | None, amount: float) -> dict[str, float]:
    """Split `amount` of `main_group` into leaf-product yields based on PADD."""
    rules = LEAF_ALLOCATIONS.get(main_group)
    if not rules:
        # Group has no defined split — treat the group code itself as the leaf.
        # (Shouldn't happen for our 23-grade vocab, but defensive.)
        return {main_group: amount}
    by_padd = rules.get(padd or "", rules.get("*"))
    out: dict[str, float] = {}
    for leaf, share in by_padd.items():
        out[leaf] = out.get(leaf, 0.0) + amount * share
    return out


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_slate(refinery: dict, grade: dict) -> list[dict]:
    """Return a list of {product_code, yield_pct, source, notes} rows for
    one (refinery, grade) pair. Yields sum to ~100."""
    plant_type = detect_plant_type(refinery)
    notes_archetype: str

    # Step 1: get main-group yields
    if plant_type:
        # Skip the cuts-and-transform pipeline; use the plant-type template.
        main_yields = dict(PLANT_TYPE_TEMPLATES[plant_type])  # copy
        notes_archetype = f"plant_type={plant_type}"
    else:
        cuts = CUT_PROFILES_BY_GRADE.get(grade["commodity"])
        if cuts is None:
            cuts = fallback_cut_profile(grade.get("sweet_sour"), grade.get("density_class"))
        archetype = determine_archetype(
            refinery.get("nci"),
            refinery.get("has_coker"),
            refinery.get("has_hydrocracker"),
        )
        transform = ARCHETYPE_TRANSFORMS[archetype]

        main_yields = {}
        for cut, cut_pct in cuts.items():
            for product, frac in transform[cut].items():
                main_yields[product] = main_yields.get(product, 0.0) + cut_pct * frac
        notes_archetype = f"archetype={archetype}"

    # Step 2: allocate each main group to leaf products
    leaf_yields: dict[str, float] = {}
    for group, amount in main_yields.items():
        for leaf, leaf_amount in allocate_leaf(group, refinery.get("padd"), amount).items():
            leaf_yields[leaf] = leaf_yields.get(leaf, 0.0) + leaf_amount

    # Step 3: normalize to 100 (small loss/gain absorbed). We keep two decimals.
    total = sum(leaf_yields.values())
    if total == 0:
        return []
    leaf_yields = {k: v * 100.0 / total for k, v in leaf_yields.items()}

    # Step 4: drop near-zero rows (< 0.05%) and renormalize once
    leaf_yields = {k: v for k, v in leaf_yields.items() if v >= 0.05}
    total = sum(leaf_yields.values())
    leaf_yields = {k: round(v * 100.0 / total, 3) for k, v in leaf_yields.items()}

    # Step 5: emit rows
    base_note = notes_archetype
    if grade.get("commodity") not in CUT_PROFILES_BY_GRADE and not plant_type:
        base_note += "; cut_profile=fallback"
    rows = []
    for product, pct in sorted(leaf_yields.items(), key=lambda kv: -kv[1]):
        rows.append({
            "product_code": product,
            "yield_pct":    pct,
            "source":       "algorithmic_baseline",
            "notes":        base_note,
        })
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("[1] fetching refinery + grade assignments from DB")
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(r"""
            SELECT
                a.asset_id, a.name,
                a.attributes->'configuration'->>'site'       AS site,
                a.attributes->'configuration'->>'operator'   AS operator,
                (a.attributes->'configuration'->>'nelson_complexity_index')::NUMERIC AS nci,
                (a.attributes->'configuration'->>'has_coker')::BOOLEAN AS has_coker,
                (a.attributes->'configuration'->>'has_hydrocracker')::BOOLEAN AS has_hydrocracker,
                l.padd
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.asset_id LIKE 'ref\_%' ESCAPE '\'
              AND a.kind = 'physical'
            ORDER BY a.asset_id
        """)
        rcols = [c.name for c in cur.description]
        refineries = {r[0]: dict(zip(rcols, r)) for r in cur.fetchall()}
        for r in refineries.values():
            if r["nci"] is not None:
                r["nci"] = float(r["nci"])

        cur.execute("""
            SELECT rga.refinery_id, rga.commodity, rga.is_primary,
                   c.sweet_sour, c.density_class,
                   c.api_gravity_min, c.api_gravity_max
            FROM refineries.refinery_grade_assignments rga
            JOIN oil_network.commodities c ON c.commodity = rga.commodity
            ORDER BY rga.refinery_id, rga.is_primary DESC, rga.commodity
        """)
        gcols = [c.name for c in cur.description]
        pairs = [dict(zip(gcols, r)) for r in cur.fetchall()]

    print(f"    {len(refineries)} refineries, {len(pairs)} (refinery, grade) pairs")

    # Group pairs by refinery for emitting JSON
    by_refinery: dict[str, list[dict]] = {}
    for p in pairs:
        by_refinery.setdefault(p["refinery_id"], []).append(p)

    print("[2] generating slates")
    n_rows = 0
    output_refineries: list[dict] = []
    for rid in sorted(by_refinery):
        ref = refineries.get(rid)
        if not ref:
            continue
        ref_entry = {
            "refinery_id": rid,
            "name":        ref["name"],
            "context": {
                "operator":         ref["operator"],
                "site":             ref["site"],
                "nci":              ref["nci"],
                "has_coker":        ref["has_coker"],
                "has_hydrocracker": ref["has_hydrocracker"],
                "padd":             ref["padd"],
                "plant_type":       detect_plant_type(ref),
                "archetype":        None if detect_plant_type(ref) else determine_archetype(
                    ref["nci"], ref["has_coker"], ref["has_hydrocracker"]),
            },
            "grades": [],
        }
        for p in by_refinery[rid]:
            rows = generate_slate(ref, p)
            ref_entry["grades"].append({
                "commodity":  p["commodity"],
                "is_primary": p["is_primary"],
                "products":   rows,
            })
            n_rows += len(rows)
        output_refineries.append(ref_entry)

    payload = {
        "version":      "1",
        "generated_at": date.today().isoformat(),
        "kind":         "algorithmic_baseline",
        "notes": (
            "Baseline yield slates generated from crude-cut profiles + "
            "refinery archetype transformation + per-PADD leaf allocation. "
            "Web overrides collected separately and merged before loading."
        ),
        "n_refineries":      len(output_refineries),
        "n_pairs":           len(pairs),
        "n_product_rows":    n_rows,
        "refineries":        output_refineries,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[3] wrote {OUT}")
    print(f"    {payload['n_refineries']} refineries x grades = "
          f"{payload['n_pairs']} pairs, {payload['n_product_rows']} product rows")


if __name__ == "__main__":
    main()
