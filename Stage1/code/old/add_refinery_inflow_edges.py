"""
Patch: wire refinery inflow edges.

Two-tier strategy chosen 2026-05-06 after the capacity-Pareto analysis:

  - The top 50 refineries (~79% of US crude refining capacity) get explicit
    per-refinery source mapping based on geography + known supply paths.
  - The remaining 65 refineries inherit per-district default source lists,
    where some districts use sub-keyed defaults (state-keyed for West Coast,
    city-keyed for Texas Gulf Coast — Corpus Christi vs Houston/Beaumont).

The original 19 named refineries (which were manually mapped before this
patch) are NEVER touched here — their pre-existing inflow edges survive.

Architectural principle (refined 2026-05-07): an INLAND refinery cannot have
a direct inflow from a `*_imports_agg` aggregate. Foreign tankers physically
discharge at coastal terminals; from there crude moves through hubs and
pipelines to inland refineries. So:

  - Coastal districts (East Coast, Texas Gulf Coast, Louisiana Gulf Coast,
    Corpus Christi sub-area, West Coast California/Washington/Oregon/Hawaii)
    DO get the relevant `*_imports_agg` source.
  - Inland districts (Appalachian No. 1, IN-IL-KY, MN-WI-ND-SD, OK-KS-MO,
    Texas Inland, North LA-AR, New Mexico, Rocky Mountain, Alaska, Nevada)
    DO NOT — they're fed only via pipelines / hubs / local production.

Each inflow edge added by this patch is physical (the source is a real
hub / origin / import / pipeline / production node). Under the starter
scope each inflow value is `latent()` — see `assign_formulas.ipynb`
section 4a — and is constrained by the district-aggregate sum.

Idempotent: re-running drops + re-adds inflow edges for non-original-19
refineries based on the current mapping, so the result converges.
"""
import json
from datetime import datetime
from pathlib import Path

GRAPH = Path(__file__).parent / "asset_graph" / "asset_graph.json"
BACKUP = GRAPH.with_name(f"asset_graph.backup_pre_inflow_{datetime.now():%Y%m%d_%H%M%S}.json")


# Refineries that came in with their own pre-existing manual mappings — never
# touched by this patch.
ORIGINAL_NAMED_REFINERIES = {
    "ref_bp_cherry_point", "ref_bp_whiting", "ref_chevron_richmond",
    "ref_citgo_lake_charles", "ref_exxon_baytown", "ref_exxon_joliet",
    "ref_galveston_bay_mpc", "ref_hf_sinclair_wy", "ref_marathon_catlettsburg",
    "ref_marathon_garyville", "ref_marathon_la", "ref_motiva_port_arthur",
    "ref_p66_bayway", "ref_p66_ponca_city", "ref_p66_wood_river",
    "ref_pbf_delaware_city", "ref_pbf_toledo", "ref_salt_lake_cluster",
    "ref_suncor_commerce_city",
}


# Explicit per-refinery source mapping for top-50 refineries that are
# *not* in the original 19 (the original 19's wiring is preserved).
TOP_50_SOURCES = {
    # ---- PADD 3 Texas Gulf Coast (R3B) — coastal ----
    "ref_marathon_galveston_bay":   ["houston_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_exxonmobil_beaumont":      ["nederland_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_deer_deer_park":           ["houston_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_valero_corpus_christi":    ["corpus_christi_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_flint_corpus_christi_west":["corpus_christi_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_phillips_sweeny":          ["houston_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_houston_houston":          ["houston_hub", "padd3_imports_agg"],
    "ref_valero_houston":           ["houston_hub", "padd3_imports_agg"],
    "ref_citgo_corpus_christi":     ["corpus_christi_hub", "padd3_imports_agg", "gulf_of_america"],

    # ---- PADD 3 Louisiana Gulf Coast (R3C) — coastal ----
    "ref_exxonmobil_baton_rouge":   ["st_james_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_chevron_pascagoula":       ["padd3_imports_agg", "gulf_of_america", "st_james_hub"],
    "ref_phillips_westlake":        ["nederland_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_shell_norco":              ["st_james_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_chalmette_chalmette":      ["st_james_hub", "padd3_imports_agg", "gulf_of_america"],
    "ref_valero_norco":             ["st_james_hub", "padd3_imports_agg"],
    "ref_valero_meraux":            ["st_james_hub", "padd3_imports_agg"],

    # ---- PADD 1 East Coast (REC) — coastal ----
    "ref_monroe_trainer":           ["padd1_imports_agg"],
    "ref_paulsboro_paulsboro":      ["padd1_imports_agg"],

    # ---- PADD 2 Indiana-Illinois-Kentucky (R2A) — inland; no imports ----
    "ref_marathon_robinson":        ["cushing_hub", "patoka_hub"],
    "ref_pdv_lemont":               ["clearbrook_entry", "patoka_hub", "cushing_hub"],
    "ref_lima_lima":                ["patoka_hub", "cushing_hub"],
    "ref_valero_memphis":           ["patoka_hub", "st_james_hub"],
    "ref_marathon_detroit":         ["clearbrook_entry", "patoka_hub"],

    # ---- PADD 3 Texas Inland (R3A) — inland; no imports ----
    "ref_diamond_sunray":           ["cushing_hub", "midland_origin"],
    "ref_wrb_borger":               ["cushing_hub", "midland_origin"],
    "ref_western_el_paso":          ["wink_origin", "midland_origin"],

    # ---- PADD 2 Oklahoma-Kansas-Missouri (R2C) — inland ----
    "ref_hf_el_dorado":             ["cushing_hub"],
    "ref_cvr_coffeyville":          ["cushing_hub"],

    # ---- PADD 5 West Coast (R50) — California: coastal + own production ----
    "ref_chevron_el_segundo":       ["padd5_imports_agg", "valdez_origin", "california_conventional"],
    "ref_torrance_torrance":        ["padd5_imports_agg", "valdez_origin", "california_conventional"],
    "ref_martinez_martinez":        ["padd5_imports_agg", "valdez_origin", "california_conventional"],
    "ref_valero_benicia":           ["padd5_imports_agg", "valdez_origin", "california_conventional"],
    "ref_hf_anacortes":             ["padd5_imports_agg", "valdez_origin", "pipe_trans_mountain_tmx"],

    # ---- PADD 2 Minnesota-Wisconsin-ND-SD (R2B) — inland; pipeline-fed ----
    "ref_flint_saint_paul":         ["clearbrook_entry", "bakken_nd_gathering"],
}


# Per-district default source lists. Some districts use a sub-keyed dict
# (state for West Coast, site for Texas Gulf Coast) because sub-areas have
# materially different supply geographies despite EIA grouping them together.
#
# Architectural principle: only coastal districts get *_imports_agg sources;
# inland districts are fed via pipelines / hubs / local production only.
DISTRICT_DEFAULT_SOURCES = {
    # PADD 1
    "East Coast":                                   ["padd1_imports_agg"],            # coastal
    "Appalachian No. 1":                            ["patoka_hub"],                   # inland — NO imports
    # PADD 2 — all inland; pipeline-fed only
    "Indiana-Illinois-Kentucky":                    ["patoka_hub", "cushing_hub", "clearbrook_entry"],
    "Minnesota-Wisconsin-North and South Dakota":   ["clearbrook_entry", "bakken_nd_gathering"],
    "Oklahoma-Kansas-Missouri":                     ["cushing_hub"],
    # PADD 3
    "Texas Inland":                                 ["midland_origin", "wink_origin", "cushing_hub"],
    "Texas Gulf Coast": {  # site-keyed — Corpus Christi differs from Houston/Beaumont area
        "CORPUS CHRISTI":      ["corpus_christi_hub", "gulf_of_america", "padd3_imports_agg"],
        "CORPUS CHRISTI EAST": ["corpus_christi_hub", "gulf_of_america", "padd3_imports_agg"],
        "CORPUS CHRISTI WEST": ["corpus_christi_hub", "gulf_of_america", "padd3_imports_agg"],
        "_default":            ["houston_hub", "nederland_hub", "padd3_imports_agg", "gulf_of_america"],
    },
    "Louisiana Gulf Coast":                         ["st_james_hub", "padd3_imports_agg", "gulf_of_america"],
    "North Louisiana-Arkansas":                     ["cushing_hub", "st_james_hub"],  # inland — NO imports
    "New Mexico":                                   ["wink_origin", "permian_nm"],    # inland
    # PADD 4 — all inland; added bakken_nd_gathering for MT refineries
    "Rocky Mountain":                               ["guernsey_hub", "pipe_express_platte", "colorado_conventional", "wyoming_conventional", "bakken_nd_gathering"],
    # PADD 5 — state-keyed because the district spans CA/WA/OR/AK/HI/NV with very different supply chains
    "West Coast": {
        "California": ["padd5_imports_agg", "valdez_origin", "california_conventional"],  # coastal + own production
        "Washington": ["padd5_imports_agg", "valdez_origin", "pipe_trans_mountain_tmx"],  # coastal + Canadian via TMX
        "Oregon":     ["padd5_imports_agg", "valdez_origin"],                              # coastal
        "Alaska":     ["alaska_north_slope"],                                              # inland (vs continental US); local production only
        "Hawaii":     ["padd5_imports_agg", "valdez_origin"],                              # island; tanker imports only (+ historical ANS)
        "Nevada":     ["padd5_imports_agg"],                                               # tiny niche refinery
        "_default":   ["padd5_imports_agg", "valdez_origin"],
    },
}


def resolve_sources(district: str | None, site: str | None, state: str | None) -> list[str]:
    """Look up source list for a refinery from the district default mapping."""
    if district is None or district not in DISTRICT_DEFAULT_SOURCES:
        return []
    entry = DISTRICT_DEFAULT_SOURCES[district]
    if isinstance(entry, list):
        return entry
    # entry is a dict: West Coast keyed by state, Texas Gulf Coast by site.
    if district == "West Coast":
        return entry.get(state, entry.get("_default", []))
    if district == "Texas Gulf Coast":
        return entry.get(site, entry.get("_default", []))
    return entry.get("_default", [])


def make_inflow_edge(source_id: str, target_id: str) -> dict:
    return {
        "id": f"e_{source_id}__{target_id}__refinery_inflow",
        "source": source_id,
        "target": target_id,
        "edge_type": "refinery_inflow",
        "attributes": {
            "notes": f"Refinery inflow from {source_id}; latent under starter scope, constrained by district aggregate.",
        },
    }


def main():
    raw = GRAPH.read_text(encoding="utf-8")
    BACKUP.write_text(raw, encoding="utf-8")
    print(f"backup -> {BACKUP.name}")
    g = json.loads(raw)
    n_before = (g["meta"]["node_count"], g["meta"]["edge_count"])

    by_id = {n["id"]: n for n in g["nodes"]}
    refineries = [n for n in g["nodes"] if n.get("node_subtype") == "refinery"]
    rewire_targets = {n["id"] for n in refineries if n["id"] not in ORIGINAL_NAMED_REFINERIES}

    # 1. Drop existing refinery_inflow edges for any rewire-eligible refinery.
    #    This makes the patch idempotent — every run converges to the current mapping.
    n_dropped = sum(
        1 for e in g["edges"]
        if e.get("edge_type") == "refinery_inflow" and e.get("target") in rewire_targets
    )
    g["edges"] = [
        e for e in g["edges"]
        if not (e.get("edge_type") == "refinery_inflow" and e.get("target") in rewire_targets)
    ]
    print(f"  dropped {n_dropped} existing refinery_inflow edges (for re-wiring)")

    # 2. Add fresh inflow edges from the updated mapping.
    n_top50_added    = 0
    n_district_added = 0
    n_unmatched      = []
    edges_added: list[dict] = []
    existing_edge_ids = {e["id"] for e in g["edges"]}

    for ref in refineries:
        rid = ref["id"]
        if rid in ORIGINAL_NAMED_REFINERIES:
            continue

        if rid in TOP_50_SOURCES:
            sources = TOP_50_SOURCES[rid]
            scope = "top50"
        else:
            cfg = ref.get("configuration") or {}
            district = cfg.get("rdist_label")
            site = cfg.get("site")
            geo = ref.get("geography") or {}
            state = geo.get("state")
            sources = resolve_sources(district, site, state)
            if not sources:
                n_unmatched.append((rid, district, site, state))
                continue
            scope = "district_default"

        # Validate sources exist in the graph
        valid = [s for s in sources if s in by_id]
        if not valid:
            n_unmatched.append((rid, sources))
            continue

        for src in valid:
            edge = make_inflow_edge(src, rid)
            if edge["id"] in existing_edge_ids:
                continue
            edges_added.append(edge)
            existing_edge_ids.add(edge["id"])
            if scope == "top50":
                n_top50_added += 1
            else:
                n_district_added += 1

    g["edges"].extend(edges_added)

    # Recount meta
    g["meta"]["node_count"] = len(g["nodes"])
    g["meta"]["edge_count"] = len(g["edges"])
    et = {}
    for e in g["edges"]:
        et[e["edge_type"]] = et.get(e["edge_type"], 0) + 1
    g["meta"]["edge_type_counts"] = et

    print()
    print("=== Result ===")
    print(f"  refineries skipped (original 19 — preserved):   {len(ORIGINAL_NAMED_REFINERIES)}")
    print(f"  edges added under top-50 explicit mapping:       {n_top50_added}")
    print(f"  edges added under per-district default mapping:  {n_district_added}")
    if n_unmatched:
        print(f"  WARN: {len(n_unmatched)} unmatched refineries:")
        for entry in n_unmatched:
            print(f"    {entry}")
    print()
    print(f"  nodes: {n_before[0]} -> {g['meta']['node_count']} (unchanged)")
    print(f"  edges: {n_before[1]} -> {g['meta']['edge_count']}")
    print(f"  refinery_inflow edges in graph: {et.get('refinery_inflow', 0)}")

    GRAPH.write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {GRAPH.name}")


if __name__ == "__main__":
    main()
