"""
Routing fixes resulting from the node-by-node sanity audit (2026-04-29).

Adds:
  - Guernsey hub (Wyoming): aggregation point for Rockies + Bakken-adjacent flows
  - Pony Express pipeline: Guernsey -> Cushing
  - Suncor Commerce City refinery: 98 kbd, Denver area (closes Colorado mass balance)
  - Phillips 66 Ponca City refinery: 210 kbd, OK (closes Oklahoma local refining)

Plus 8 new flow edges:
  - wyoming_conventional -> guernsey_hub          (WY production into Pony Express system)
  - colorado_conventional -> guernsey_hub         (CO surplus via White Cliffs into Pony Express)
  - colorado_conventional -> ref_suncor_commerce_city  (local CO refinery)
  - oklahoma_conventional -> ref_p66_ponca_city   (local OK refinery; keeps OK -> cushing too)
  - bakken_nd_gathering -> clearbrook_entry       (opens Bakken -> Enbridge Mainline -> Whiting)
  - gulf_of_america -> st_james_hub               (offshore -> Garyville path)
  - loop_terminal -> ref_marathon_garyville       (LOOP marine to Garyville)
  - wyoming_conventional -> ref_salt_lake_cluster (primary Salt Lake feed)

Plus pipeline-intake/outflow pair for Pony Express, plus 2 aggregation edges for the new refineries.

Idempotent: re-running adds missing entries only.
"""
import json
from collections import Counter
from pathlib import Path

JSON_PATH = Path("asset_graph/asset_graph.json")
G = json.loads(JSON_PATH.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# 1. New physical nodes
# ---------------------------------------------------------------------------
NEW_NODES = [
    # Guernsey hub: storage + crude trading hub in Wyoming, aggregation point
    # for Pony Express, Platte, and other Rockies/Bakken-adjacent pipelines.
    {
        "id": "guernsey_hub",
        "name": "Guernsey storage hub (Wyoming)",
        "node_class": "infrastructure",
        "node_subtype": "storage_terminal",
        "geography": {"lat": 42.2683, "lon": -104.7414, "county": "Platte",
                      "state": "WY", "padd": "PADD4", "country": "US"},
        "resolution_hierarchy": {"parent": None, "children": [], "aggregation": "sum"},
        "configuration": {
            "terminal_type": "trading_hub",
            "storage_capacity_mbbl": 3500,
            "storage_type": "above_ground_tank_farm",
            "inbound_pipelines": ["platte", "white_cliffs", "frontier", "pony_express_origin"],
            "outbound_pipelines": ["pony_express", "platte_east"],
            "operator": "multiple (Tallgrass Energy, Spectra/Enbridge, Plains, NOVA)",
        },
        "starter_status": "authoritative",
        "notes": "Major Rockies crude aggregator; Pony Express and Platte pipelines load here for shipment to Cushing (PADD2) and other markets.",
    },
    # Pony Express pipeline: Guernsey -> Cushing (~320 kbd capacity, Tallgrass).
    {
        "id": "pipe_pony_express",
        "name": "Pony Express Pipeline (Guernsey to Cushing)",
        "node_class": "infrastructure",
        "node_subtype": "pipeline",
        "geography": {"lat": None, "lon": None, "county": None,
                      "state": None, "padd": ["PADD4", "PADD2"], "country": "US",
                      "endpoint_intake_refs":  ["guernsey_hub"],
                      "endpoint_outflow_refs": ["cushing_hub"],
                      "spans_multiple_padds":  True},
        "resolution_hierarchy": {"parent": None, "children": [], "aggregation": None},
        "configuration": {
            "operator": "Tallgrass Energy",
            "diameter_in": 24,
            "length_miles": 698,
            "capacity_bpd": 320000,
            "transit_days": 4,
            "direction": "Guernsey -> Cushing",
        },
        "starter_status": "authoritative",
        "notes": "Carries Wyoming/Bakken-adjacent crude from Guernsey south to Cushing.",
    },
    # Suncor Commerce City refinery (Denver area).
    {
        "id": "ref_suncor_commerce_city",
        "name": "Suncor Commerce City Refinery",
        "node_class": "infrastructure",
        "node_subtype": "refinery",
        "geography": {"lat": 39.8083, "lon": -104.9525, "county": "Adams",
                      "state": "CO", "padd": "PADD4", "country": "US"},
        "resolution_hierarchy": {"parent": "padd4_refining_view", "children": [], "aggregation": None},
        "configuration": {
            "operator": "Suncor Energy",
            "capacity_bd": 98000,
            "nelson_complexity_index": 9.0,
            "has_fcc": True,
            "has_hydrocracker": False,
            "has_coker": False,
            "preferred_slate": "light_sweet",
        },
        "starter_status": "authoritative",
        "notes": "Sole Denver-area refinery; processes local DJ Basin / Wattenberg crude.",
    },
    # Phillips 66 Ponca City refinery (northern OK).
    {
        "id": "ref_p66_ponca_city",
        "name": "Phillips 66 Ponca City Refinery",
        "node_class": "infrastructure",
        "node_subtype": "refinery",
        "geography": {"lat": 36.7140, "lon": -97.0586, "county": "Kay",
                      "state": "OK", "padd": "PADD2", "country": "US"},
        "resolution_hierarchy": {"parent": "padd2_refining_view", "children": [], "aggregation": None},
        "configuration": {
            "operator": "Phillips 66",
            "capacity_bd": 210000,
            "nelson_complexity_index": 13.0,
            "has_fcc": True,
            "has_hydrocracker": True,
            "has_coker": True,
            "preferred_slate": "medium_sour",
        },
        "starter_status": "authoritative",
        "notes": "Receives crude via Plains All American gathering systems + Cushing connections.",
    },
]

# ---------------------------------------------------------------------------
# 2. New edges
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

# Aggregation edges (parent_view -> new refinery), non-flow.
NEW_EDGES.append(edge("padd2_refining_view", "ref_p66_ponca_city", "aggregation",
                      notes="Refinery rolls up into PADD-level refining centre view."))
NEW_EDGES.append(edge("padd4_refining_view", "ref_suncor_commerce_city", "aggregation",
                      notes="Refinery rolls up into PADD-level refining centre view."))

# Wyoming + Colorado production -> Guernsey (production_outflow).
NEW_EDGES.append(edge("wyoming_conventional", "guernsey_hub", "production_outflow",
                      capacity_bpd=None, transit_time_days=1,
                      notes="Wyoming production aggregated at Guernsey via Frontier/Platte gathering systems."))
NEW_EDGES.append(edge("colorado_conventional", "guernsey_hub", "production_outflow",
                      capacity_bpd=215000, transit_time_days=1,
                      notes="Colorado DJ Basin / Wattenberg crude via White Cliffs to Guernsey for southbound dispatch."))

# Colorado local refining: Suncor Commerce City absorbs ~98 kbd locally.
NEW_EDGES.append(edge("colorado_conventional", "ref_suncor_commerce_city", "production_outflow",
                      capacity_bpd=98000, transit_time_days=0.5,
                      notes="DJ Basin / Wattenberg crude via local gathering to Suncor Commerce City; only Denver-area refinery."))

# Wyoming production -> Salt Lake (primary refinery feed, was missing).
NEW_EDGES.append(edge("wyoming_conventional", "ref_salt_lake_cluster", "production_outflow",
                      capacity_bpd=None, transit_time_days=1,
                      notes="Wyoming sweet crude via Frontier and local pipelines to Salt Lake refining cluster (UT/WY's primary refinery feed)."))

# Oklahoma local refining: Ponca City (was missing).
NEW_EDGES.append(edge("oklahoma_conventional", "ref_p66_ponca_city", "production_outflow",
                      capacity_bpd=210000, transit_time_days=0.5,
                      notes="Oklahoma production via Plains gathering directly to Ponca City refinery; bypasses Cushing for local volumes."))

# Bakken -> Clearbrook (opens the Enbridge mainline route to Whiting/Catlettsburg/Toledo).
NEW_EDGES.append(edge("bakken_nd_gathering", "clearbrook_entry", "gathering",
                      capacity_bpd=900000, transit_time_days=2,
                      notes="Bakken-ND production routed via gathering systems to Clearbrook MN (Enbridge mainline injection point)."))

# Gulf offshore -> St. James (offshore-to-Garyville path).
NEW_EDGES.append(edge("gulf_of_america", "st_james_hub", "offshore_outflow",
                      capacity_bpd=400000, transit_time_days=2,
                      notes="GoM offshore platform pipelines (Capline-area) landing at St. James for refinery dispatch."))

# LOOP marine terminal -> Garyville refinery (LOCAP onward).
NEW_EDGES.append(edge("loop_terminal", "ref_marathon_garyville", "terminal_connector",
                      capacity_bpd=600000, transit_time_days=0.5,
                      notes="LOCAP delivers LOOP-imported and offshore Gulf crude to Garyville refinery."))

# Pony Express pipeline pair (intake at Guernsey, outflow at Cushing).
NEW_EDGES.append(edge("guernsey_hub", "pipe_pony_express", "pipeline_intake",
                      capacity_bpd=320000, transit_days=0.0,
                      notes="(Pony Express Pipeline intake)"))
NEW_EDGES.append(edge("pipe_pony_express", "cushing_hub", "pipeline_outflow",
                      capacity_bpd=320000, transit_days=4, direction=None,
                      notes="(Pony Express Pipeline outflow)"))

# ---------------------------------------------------------------------------
# 3. Update existing parent aggregates' resolution_hierarchy.children
# ---------------------------------------------------------------------------
HIERARCHY_UPDATES = {
    "padd2_refining_view": "ref_p66_ponca_city",
    "padd4_refining_view": "ref_suncor_commerce_city",
}

# ---------------------------------------------------------------------------
# Apply (idempotent)
# ---------------------------------------------------------------------------
existing_node_ids = {n["id"] for n in G["nodes"]}
existing_edge_ids = {e["id"] for e in G["edges"]}

added_nodes, added_edges = [], []
for node in NEW_NODES:
    if node["id"] not in existing_node_ids:
        G["nodes"].append(node)
        added_nodes.append(node["id"])

for e in NEW_EDGES:
    if e["id"] not in existing_edge_ids:
        G["edges"].append(e)
        added_edges.append(e["id"])

# Patch parent aggregates' children lists.
for parent_id, child_id in HIERARCHY_UPDATES.items():
    for n in G["nodes"]:
        if n["id"] == parent_id:
            children = n["resolution_hierarchy"].setdefault("children", [])
            if child_id not in children:
                children.append(child_id)

# Refresh meta counts.
G["meta"]["node_subtype_counts"] = dict(Counter(n["node_subtype"] for n in G["nodes"]))
G["meta"]["edge_type_counts"]    = dict(Counter(e["edge_type"]    for e in G["edges"]))

JSON_PATH.write_text(json.dumps(G, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"Added {len(added_nodes)} nodes: {added_nodes}")
print(f"Added {len(added_edges)} edges:")
for eid in added_edges:
    print(f"  {eid}")
print(f"\nTotals: {len(G['nodes'])} nodes, {len(G['edges'])} edges")
print(f"\nNode subtype counts:")
for k, v in sorted(G["meta"]["node_subtype_counts"].items()):
    print(f"  {k:30s} {v}")
print(f"\nEdge type counts:")
for k, v in sorted(G["meta"]["edge_type_counts"].items()):
    print(f"  {k:30s} {v}")
