"""
Add the refinery layer to asset_graph.json.

Adds:
  - 5 abstract refining-centre aggregates (one per PADD)
  - 17 physical refineries with capacity_bd, NCI, conversion-unit flags
  - 17 aggregation edges (parent_view -> physical refinery)
  - 30 flow edges (production->gathering, hub->refinery, conventional->refinery,
    offshore->hub, imports->refinery, valdez->west-coast)

Run from Thesis/Code/.  Idempotent: re-running adds missing entries only.
"""
import json
from pathlib import Path

JSON_PATH = Path("asset_graph/asset_graph.json")
G = json.loads(JSON_PATH.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# 5 abstract refining-centre aggregates
# ---------------------------------------------------------------------------
def aggregate(node_id, name, padd, lat, lon, children, capacity_bd):
    return {
        "id": node_id,
        "name": name,
        "node_class": "observational",
        "node_subtype": "refining_centre_view",
        "geography": {"lat": lat, "lon": lon, "county": None, "state": None,
                      "padd": padd, "country": "US"},
        "resolution_hierarchy": {"parent": None, "children": children,
                                 "aggregation": "sum"},
        "configuration": {
            "filter": f"node_subtype == 'refinery' AND location.padd == {padd}",
            "variable_scope": "C (consumption / refinery runs) over authoritative refinery nodes",
            "regional_capacity_bd": capacity_bd,
        },
        "starter_status": "derived",
        "notes": "Pure view; no edges. Computed at query time from authoritative refinery nodes.",
    }

# ---------------------------------------------------------------------------
# 17 physical refineries
# Capacity (kb/d) and Nelson Complexity Index from public 2023-2024 sources
# (EIA refinery capacity tables, company 10-Ks, OGJ surveys). NCI is approximate.
# ---------------------------------------------------------------------------
def refinery(node_id, name, lat, lon, county, state, padd,
             capacity_kbd, nci, operator,
             has_fcc=True, has_hydrocracker=False, has_coker=False,
             slate="medium_sour", parent_view=None, notes=""):
    return {
        "id": node_id,
        "name": name,
        "node_class": "infrastructure",
        "node_subtype": "refinery",
        "geography": {"lat": lat, "lon": lon, "county": county, "state": state,
                      "padd": padd, "country": "US"},
        "resolution_hierarchy": {"parent": parent_view, "children": [],
                                 "aggregation": None},
        "configuration": {
            "operator": operator,
            "capacity_bd": capacity_kbd * 1000,
            "nelson_complexity_index": nci,
            "has_fcc": has_fcc,
            "has_hydrocracker": has_hydrocracker,
            "has_coker": has_coker,
            "preferred_slate": slate,
        },
        "starter_status": "authoritative",
        "notes": notes,
    }

REFINERIES = [
    # Gulf Coast (PADD 3)
    refinery("ref_motiva_port_arthur", "Motiva Port Arthur Refinery",
             29.8732, -93.9358, "Jefferson", "TX", "PADD3", 626, 10.5,
             "Motiva (Saudi Aramco)", has_coker=True, slate="heavy_sour",
             parent_view="padd3_refining_view",
             notes="Largest US refinery by capacity. Coker-equipped, processes heavy sour crudes."),
    refinery("ref_galveston_bay_mpc", "Marathon Galveston Bay Refinery",
             29.3838, -94.9311, "Galveston", "TX", "PADD3", 593, 12.0,
             "Marathon Petroleum", has_hydrocracker=True, has_coker=True, slate="medium_sour",
             parent_view="padd3_refining_view"),
    refinery("ref_exxon_baytown", "ExxonMobil Baytown Refinery",
             29.7421, -95.0014, "Harris", "TX", "PADD3", 561, 13.0,
             "ExxonMobil", has_hydrocracker=True, has_coker=True, slate="medium_sour",
             parent_view="padd3_refining_view",
             notes="One of the most complex US refineries; integrated petrochemicals."),
    refinery("ref_marathon_garyville", "Marathon Garyville Refinery",
             30.0577, -90.6263, "St. John the Baptist", "LA", "PADD3", 597, 12.5,
             "Marathon Petroleum", has_hydrocracker=True, has_coker=True, slate="heavy_sour",
             parent_view="padd3_refining_view",
             notes="Receives Capline-reversed flow at St. James."),
    refinery("ref_citgo_lake_charles", "CITGO Lake Charles Refinery",
             30.2229, -93.2974, "Calcasieu", "LA", "PADD3", 419, 10.0,
             "CITGO (PDVSA)", has_coker=True, slate="heavy_sour",
             parent_view="padd3_refining_view"),
    # Midwest (PADD 2)
    refinery("ref_bp_whiting", "BP Whiting Refinery",
             41.6714, -87.4798, "Lake", "IN", "PADD2", 435, 12.5,
             "BP", has_hydrocracker=True, has_coker=True, slate="heavy_sour",
             parent_view="padd2_refining_view",
             notes="Modernised 2013 to process heavy Canadian crude."),
    refinery("ref_p66_wood_river", "Phillips 66 / Cenovus Wood River Refinery",
             38.8400, -90.0750, "Madison", "IL", "PADD2", 356, 12.0,
             "WRB Refining (Phillips 66 / Cenovus JV)", has_coker=True, slate="heavy_sour",
             parent_view="padd2_refining_view"),
    refinery("ref_exxon_joliet", "ExxonMobil Joliet Refinery",
             41.4076, -88.1881, "Will", "IL", "PADD2", 271, 9.0,
             "ExxonMobil", slate="medium_sour",
             parent_view="padd2_refining_view"),
    refinery("ref_marathon_catlettsburg", "Marathon Catlettsburg Refinery",
             38.4060, -82.6113, "Boyd", "KY", "PADD2", 291, 13.0,
             "Marathon Petroleum", has_hydrocracker=True, has_coker=True, slate="medium_sour",
             parent_view="padd2_refining_view"),
    refinery("ref_pbf_toledo", "PBF Toledo Refinery",
             41.6740, -83.4944, "Lucas", "OH", "PADD2", 170, 9.5,
             "PBF Energy", slate="medium_sour",
             parent_view="padd2_refining_view"),
    # West Coast (PADD 5)
    refinery("ref_marathon_la", "Marathon Los Angeles Refinery (Carson + Wilmington)",
             33.8267, -118.2546, "Los Angeles", "CA", "PADD5", 363, 13.0,
             "Marathon Petroleum", has_hydrocracker=True, has_coker=True, slate="medium_sour",
             parent_view="padd5_refining_view",
             notes="Combined Carson + Wilmington operations after 2017 acquisition."),
    refinery("ref_chevron_richmond", "Chevron Richmond Refinery",
             37.9356, -122.3920, "Contra Costa", "CA", "PADD5", 245, 12.0,
             "Chevron", has_hydrocracker=True, slate="medium_sour",
             parent_view="padd5_refining_view"),
    refinery("ref_bp_cherry_point", "BP Cherry Point Refinery",
             48.8730, -122.7592, "Whatcom", "WA", "PADD5", 251, 10.0,
             "BP", has_coker=True, slate="medium_sour",
             parent_view="padd5_refining_view",
             notes="Receives Canadian crude via Trans Mountain Pipeline (not modelled)."),
    # East Coast (PADD 1)
    refinery("ref_p66_bayway", "Phillips 66 Bayway Refinery",
             40.6503, -74.2107, "Union", "NJ", "PADD1", 258, 10.5,
             "Phillips 66", has_coker=True, slate="medium_sour",
             parent_view="padd1_refining_view",
             notes="Tanker-fed; primary US East Coast refinery."),
    refinery("ref_pbf_delaware_city", "PBF Delaware City Refinery",
             39.5709, -75.5928, "New Castle", "DE", "PADD1", 190, 11.5,
             "PBF Energy", has_hydrocracker=True, has_coker=True, slate="heavy_sour",
             parent_view="padd1_refining_view"),
    # Rockies (PADD 4)
    refinery("ref_salt_lake_cluster", "Salt Lake City refining cluster",
             40.8742, -111.8951, "Salt Lake", "UT", "PADD4", 180, 9.0,
             "HF Sinclair / Marathon / Chevron / Big West (combined)", slate="light_sweet",
             parent_view="padd4_refining_view",
             notes="Aggregated cluster of 4 small Salt Lake refineries; processes Uinta/Wyoming sweet crudes."),
    refinery("ref_hf_sinclair_wy", "HF Sinclair Sinclair Refinery",
             41.7836, -107.1106, "Carbon", "WY", "PADD4", 80, 10.0,
             "HF Sinclair", slate="medium_sour",
             parent_view="padd4_refining_view"),
]

AGGREGATES = [
    aggregate("padd1_refining_view", "PADD 1 refining centre (East Coast)", "PADD1",
              40.0, -75.0,
              ["ref_p66_bayway", "ref_pbf_delaware_city"],
              capacity_bd=448_000),
    aggregate("padd2_refining_view", "PADD 2 refining centre (Midwest)", "PADD2",
              40.5, -87.5,
              ["ref_bp_whiting", "ref_p66_wood_river", "ref_exxon_joliet",
               "ref_marathon_catlettsburg", "ref_pbf_toledo"],
              capacity_bd=1_523_000),
    aggregate("padd3_refining_view", "PADD 3 refining centre (Gulf Coast)", "PADD3",
              29.7, -94.0,
              ["ref_motiva_port_arthur", "ref_galveston_bay_mpc", "ref_exxon_baytown",
               "ref_marathon_garyville", "ref_citgo_lake_charles"],
              capacity_bd=2_796_000),
    aggregate("padd4_refining_view", "PADD 4 refining centre (Rockies)", "PADD4",
              41.0, -109.0,
              ["ref_salt_lake_cluster", "ref_hf_sinclair_wy"],
              capacity_bd=260_000),
    aggregate("padd5_refining_view", "PADD 5 refining centre (West Coast)", "PADD5",
              37.0, -120.0,
              ["ref_marathon_la", "ref_chevron_richmond", "ref_bp_cherry_point"],
              capacity_bd=859_000),
]

# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------
def edge(src, tgt, edge_type, **attrs):
    return {
        "id": f"e_{src}__{tgt}__{edge_type}",
        "source": src,
        "target": tgt,
        "edge_type": edge_type,
        "attributes": attrs,
    }

NEW_EDGES = []

# Aggregation edges (parent_view -> child refinery), 17 total
for agg in AGGREGATES:
    for child in agg["resolution_hierarchy"]["children"]:
        NEW_EDGES.append(edge(agg["id"], child, "aggregation",
                              notes="Refinery rolls up into PADD-level refining centre view."))

# Production -> gathering (6 edges, fills the missing first link of the production chain)
PRODUCTION_TO_GATHERING = [
    ("permian_tx",         "permian_tx_gathering",  "Permian-TX wellhead production gathered by midstream systems."),
    ("permian_nm",         "permian_nm_gathering",  "Permian-NM wellhead production gathered by midstream systems."),
    ("bakken_nd",          "bakken_nd_gathering",   "Bakken-ND wellhead production gathered by Hess/Plains/Bridger systems."),
    ("bakken_mt",          "bakken_nd_gathering",   "Bakken-MT volumes are gathered by the same ND-centred systems."),
    ("eagle_ford_tx",      "eagle_ford_gathering",  "Eagle Ford wellhead production gathered by Plains/Enterprise systems."),
    ("alaska_north_slope", "ans_gathering",         "ANS wellhead production gathered into Prudhoe/Kuparuk systems."),
]
for s, t, note in PRODUCTION_TO_GATHERING:
    NEW_EDGES.append(edge(s, t, "production_to_gathering",
                          capacity_bpd=None, transit_time_days=0.5, notes=note))

# Hub -> refinery (10 edges, terminal_connector pattern matching existing hub->export)
HUB_TO_REFINERY = [
    ("houston_hub",   "ref_exxon_baytown",         800_000, "Local Houston-area takeoff to Baytown."),
    ("houston_hub",   "ref_galveston_bay_mpc",     650_000, "Texas City connectors and Houston-Galveston short pipelines."),
    ("nederland_hub", "ref_motiva_port_arthur",    700_000, "Sunoco Logistics short pipelines, Nederland to Port Arthur."),
    ("nederland_hub", "ref_citgo_lake_charles",    450_000, "Calcasieu pipeline plus dock receipts."),
    ("st_james_hub",  "ref_marathon_garyville",    600_000, "Capline-reversed crude offloaded at St. James, short connector to Garyville."),
    ("patoka_hub",    "ref_bp_whiting",            500_000, "Lakehead/Enbridge takeoff to Whiting (terminus of Mainline 6/14/61)."),
    ("patoka_hub",    "ref_p66_wood_river",        400_000, "Wood River pipeline / Mid-Valley takeoff."),
    ("patoka_hub",    "ref_exxon_joliet",          300_000, "Mokena pipeline takeoff from Patoka/Mainline."),
    ("patoka_hub",    "ref_marathon_catlettsburg", 350_000, "Mid-Valley pipeline Patoka -> Catlettsburg."),
    ("patoka_hub",    "ref_pbf_toledo",            200_000, "Mid-Valley takeoff and Enbridge/Capline connections."),
]
for s, t, cap, note in HUB_TO_REFINERY:
    NEW_EDGES.append(edge(s, t, "terminal_connector",
                          capacity_bpd=cap, transit_time_days=0.5, notes=note))

# Imports -> refinery (5 shipping_route edges)
IMPORT_TO_REFINERY = [
    ("padd1_imports_agg", "ref_p66_bayway",          "Foreign tanker imports landing at Bayway."),
    ("padd1_imports_agg", "ref_pbf_delaware_city",   "Foreign tanker imports landing at Delaware Bay."),
    ("padd5_imports_agg", "ref_bp_cherry_point",     "Foreign tanker imports plus Trans Mountain crude (TMX not modelled)."),
    ("padd5_imports_agg", "ref_marathon_la",         "Foreign tanker imports to LA basin."),
    ("padd5_imports_agg", "ref_chevron_richmond",    "Foreign tanker imports to Richmond Long Wharf."),
]
for s, t, note in IMPORT_TO_REFINERY:
    NEW_EDGES.append(edge(s, t, "shipping_route",
                          vessel_class="VLCC", transit_time_days=15, notes=note))

# Alaska -> West Coast refineries (2 shipping edges, ANS tanker route)
NEW_EDGES.append(edge("valdez_origin", "ref_marathon_la", "shipping_route",
                      vessel_class="Aframax", transit_time_days=6,
                      notes="ANS crude tanker voyage Valdez -> LA basin refineries."))
NEW_EDGES.append(edge("valdez_origin", "ref_chevron_richmond", "shipping_route",
                      vessel_class="Aframax", transit_time_days=7,
                      notes="ANS crude tanker voyage Valdez -> Bay Area refineries."))

# State conventional -> refinery / hub (5 edges, no pipeline node modelled)
NEW_EDGES.append(edge("california_conventional", "ref_marathon_la", "production_outflow",
                      capacity_bpd=None, transit_time_days=0.5,
                      notes="Local CA gathering systems (Plains, Crimson) -> LA basin refineries; not modelled as pipeline nodes."))
NEW_EDGES.append(edge("california_conventional", "ref_chevron_richmond", "production_outflow",
                      capacity_bpd=None, transit_time_days=0.5,
                      notes="Local CA gathering systems -> Bay Area refineries; not modelled as pipeline nodes."))
NEW_EDGES.append(edge("oklahoma_conventional", "cushing_hub", "production_outflow",
                      capacity_bpd=None, transit_time_days=1,
                      notes="Plains All American Oklahoma gathering -> Cushing; not modelled as pipeline nodes."))
NEW_EDGES.append(edge("wyoming_conventional", "ref_hf_sinclair_wy", "production_outflow",
                      capacity_bpd=None, transit_time_days=1,
                      notes="Local WY gathering -> Sinclair refinery; not modelled as pipeline nodes."))
NEW_EDGES.append(edge("colorado_conventional", "ref_salt_lake_cluster", "production_outflow",
                      capacity_bpd=None, transit_time_days=2,
                      notes="White Cliffs / Pony Express systems -> Rockies refining; not modelled as pipeline nodes."))

# Offshore -> hub / terminal (2 edges)
NEW_EDGES.append(edge("gulf_of_america", "houston_hub", "offshore_outflow",
                      capacity_bpd=2_000_000, transit_time_days=2,
                      notes="GoM offshore platform pipelines (Mars/Olympus, Endymion, BPP) landing at Houston-area hubs; not modelled as pipeline nodes."))
NEW_EDGES.append(edge("gulf_of_america", "loop_terminal", "offshore_outflow",
                      capacity_bpd=1_500_000, transit_time_days=1,
                      notes="GoM offshore pipelines into LOOP marine terminal."))

# ---------------------------------------------------------------------------
# Apply changes (idempotent: skip if id already present)
# ---------------------------------------------------------------------------
existing_node_ids = {n["id"] for n in G["nodes"]}
existing_edge_ids = {e["id"] for e in G["edges"]}

added_nodes = []
for node in AGGREGATES + REFINERIES:
    if node["id"] not in existing_node_ids:
        G["nodes"].append(node)
        added_nodes.append(node["id"])

added_edges = []
for e in NEW_EDGES:
    if e["id"] not in existing_edge_ids:
        G["edges"].append(e)
        added_edges.append(e["id"])

# Update meta counts
from collections import Counter
G["meta"]["node_subtype_counts"] = dict(Counter(n["node_subtype"] for n in G["nodes"]))
G["meta"]["edge_type_counts"]    = dict(Counter(e["edge_type"]    for e in G["edges"]))

# Update coverage contract: add refining-centre entry, individual refineries authoritative.
contract = G["starter_coverage_contract"]
contract.setdefault("authoritative_levels", {})
contract["authoritative_levels"]["refining_subsystem"] = {
    "level": "individual_refineries",
    "collapsed_below": [],
    "notes": "Individual refineries authoritative; PADD-level refining_centre_view is a derived rollup.",
}
contract.setdefault("notes", []).append(
    "Refinery layer: 17 physical refineries authoritative at site level; 5 PADD-level refining_centre_view nodes are derived (sum of refinery C variables)."
)

# Write back
JSON_PATH.write_text(json.dumps(G, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"Added {len(added_nodes)} nodes, {len(added_edges)} edges.")
print(f"\nNode subtype counts now:")
for k, v in sorted(G["meta"]["node_subtype_counts"].items()):
    print(f"  {k:30s} {v}")
print(f"\nEdge type counts now:")
for k, v in sorted(G["meta"]["edge_type_counts"].items()):
    print(f"  {k:30s} {v}")
print(f"\nTotals: {len(G['nodes'])} nodes, {len(G['edges'])} edges.")
