"""
Pass 4: close the refining-capacity coverage gap (35% -> ~100%) with residual
refinery nodes per PADD, plus add the missing padd3_imports_agg.

Adds:
  - 5 residual refinery nodes (one per PADD) representing the un-modelled
    refining capacity in each region. Tagged `is_residual: true` in
    configuration so downstream consumers can distinguish them from named
    refineries.
  - padd3_imports_agg: PADD3 import terminal aggregate (Mexican Maya, Saudi,
    Iraqi, Brazilian foreign tanker imports — ~2,300 kbd).

Per-PADD residual capacities chosen to close the real-vs-modelled gap:
  PADD1: 400 kbd (PBF Paulsboro 180, Monroe Trainer 190, smaller)
  PADD2: 2,200 kbd (~10 mid-size MW refineries: P66 Borger, Marathon Robinson,
                    HollyFrontier Tulsa, BP-Husky Toledo, etc.)
  PADD3: 6,700 kbd (~20 Gulf refineries: P66 Sweeny, Valero Port Arthur,
                    ExxonMobil Beaumont, Total Port Arthur, P66 Lake Charles,
                    Valero Texas City, etc. — by far the biggest aggregate)
  PADD4:   290 kbd (ExxonMobil Billings, P66 Billings, HF Cheyenne, smaller)
  PADD5: 1,700 kbd (P66 Wilmington, P66 Rodeo, Valero Benicia, Marathon
                    Anacortes, P66 Ferndale, etc.)
  TOTAL: 11,290 kbd, closing the 11,306 kbd gap.

Feeder edges follow the same hubs that feed each region's named refineries.
PADD3 also picks up the new padd3_imports_agg as a major foreign-crude source,
plus an extension to the existing Motiva Port Arthur and Citgo Lake Charles
refineries which historically run substantial foreign heavy crude.

Idempotent.
"""
import json
from collections import Counter
from pathlib import Path

JSON_PATH = Path("asset_graph/asset_graph.json")
G = json.loads(JSON_PATH.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# 1. New nodes
# ---------------------------------------------------------------------------
def residual(node_id, name, lat, lon, padd, capacity_kbd, nci, n_refineries, examples,
             slate="medium_sour"):
    return {
        "id": node_id,
        "name": name,
        "node_class": "infrastructure",
        "node_subtype": "refinery",
        "geography": {"lat": lat, "lon": lon, "county": None,
                      "state": None, "padd": padd, "country": "US"},
        "resolution_hierarchy": {"parent": f"{padd.lower()}_refining_view",
                                 "children": [], "aggregation": None},
        "configuration": {
            "operator": f"multiple ({padd} residual refining capacity not individually modelled)",
            "capacity_bd": capacity_kbd * 1000,
            "nelson_complexity_index": nci,
            "has_fcc": True,
            "has_hydrocracker": True,
            "has_coker": True,
            "preferred_slate": slate,
            "is_residual": True,
            "aggregates_n_refineries": n_refineries,
            "examples": examples,
        },
        "starter_status": "authoritative",
        "notes": (f"Residual aggregate: closes the gap between real {padd} refining "
                  f"capacity ({capacity_kbd:,} kbd over ~{n_refineries} unmodelled "
                  f"facilities) and the named refineries in this view."),
    }

NEW_NODES = [
    residual("ref_padd1_residual", "PADD 1 residual refining capacity",
             40.5, -75.3, "PADD1", 400, 11.0, 4,
             ["PBF Paulsboro NJ (180 kbd)",
              "Monroe Trainer PA (190 kbd, intermittent)",
              "smaller East Coast refineries"]),
    residual("ref_padd2_residual", "PADD 2 residual refining capacity",
             41.0, -87.0, "PADD2", 2200, 11.5, 12,
             ["Phillips 66 Borger TX",
              "Marathon Robinson IL",
              "HollyFrontier Tulsa OK",
              "BP-Husky Toledo OH",
              "Marathon Detroit MI",
              "CHS McPherson KS",
              "CVR Coffeyville KS",
              "Cenovus Lima OH",
              "smaller Midwest refineries"]),
    residual("ref_padd3_residual", "PADD 3 residual refining capacity",
             29.2, -94.5, "PADD3", 6700, 11.5, 22,
             ["Phillips 66 Sweeny TX (260 kbd)",
              "Valero Port Arthur TX (395 kbd)",
              "ExxonMobil Beaumont TX (369 kbd)",
              "Total Port Arthur TX (225 kbd)",
              "Phillips 66 Lake Charles LA (260 kbd)",
              "Valero Texas City TX (260 kbd)",
              "Marathon Texas City TX (90 kbd)",
              "Citgo Corpus Christi TX (165 kbd)",
              "Valero Corpus Christi TX (370 kbd)",
              "Flint Hills Corpus Christi TX (305 kbd)",
              "ExxonMobil Beaumont/Baytown legacy",
              "Phillips 66 Westlake LA",
              "Phillips 66 Belle Chasse LA",
              "Marathon Detroit",
              "Calumet Shreveport",
              "smaller Gulf refineries"], slate="heavy_sour"),
    residual("ref_padd4_residual", "PADD 4 residual refining capacity",
             41.5, -109.5, "PADD4", 290, 9.5, 5,
             ["ExxonMobil Billings MT (60 kbd)",
              "Phillips 66 Billings MT (60 kbd)",
              "Calumet Great Falls MT",
              "HollyFrontier Cheyenne WY (legacy)",
              "smaller Rockies refineries"]),
    residual("ref_padd5_residual", "PADD 5 residual refining capacity",
             36.5, -120.5, "PADD5", 1700, 11.0, 11,
             ["Phillips 66 Wilmington CA (139 kbd)",
              "Phillips 66 Rodeo CA (120 kbd, converting to renewables)",
              "Valero Benicia CA (170 kbd)",
              "Marathon Anacortes WA (120 kbd)",
              "Phillips 66 Ferndale WA (105 kbd)",
              "Tesoro Salt Lake CA (small)",
              "Kern Energy Bakersfield CA",
              "Valero Wilmington CA",
              "ExxonMobil Torrance CA (149 kbd)",
              "smaller West Coast refineries"]),
]

NEW_NODES.append({
    "id": "padd3_imports_agg",
    "name": "PADD 3 (Gulf Coast) import terminals (aggregate)",
    "node_class": "infrastructure",
    "node_subtype": "import_terminal",
    "geography": {"lat": 29.5, "lon": -90.0, "county": None,
                  "state": None, "padd": "PADD3", "country": "US"},
    "resolution_hierarchy": {"parent": None, "children": [], "aggregation": "sum"},
    "configuration": {
        "terminal_type": "marine_import_aggregate",
        "max_vessel_class": "VLCC_via_LOOP_plus_Aframax",
        "locations": ["Houston Ship Channel imports",
                      "Beaumont/Port Arthur tanker imports",
                      "Lake Charles tanker imports",
                      "Pascagoula tanker imports",
                      "LOOP imports (re-routed for refining)"],
        "operator": "multiple",
    },
    "starter_status": "authoritative",
    "notes": ("Aggregate Gulf Coast crude imports — Mexican Maya, Saudi, Iraqi, "
              "Brazilian, Colombian heavy + medium sour tanker imports. ~2,300 kbd "
              "in 2023/2024."),
})

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

# Aggregation: residuals roll up under their PADD refining view (parent -> child pattern)
for nid in ("ref_padd1_residual", "ref_padd2_residual", "ref_padd3_residual",
            "ref_padd4_residual", "ref_padd5_residual"):
    parent_view = nid.replace("ref_", "").replace("_residual", "_refining_view")
    NEW_EDGES.append(edge(parent_view, nid, "aggregation",
                          notes="Residual refining aggregate rolls up into PADD-level refining centre view."))

# PADD1 residual feeders (~400 kbd, primarily tanker imports)
NEW_EDGES.append(edge("padd1_imports_agg", "ref_padd1_residual", "shipping_route",
                      vessel_class="Aframax", transit_time_days=15, capacity_bpd=400_000,
                      notes="Tanker imports feeding PADD1 residual refining capacity (PBF Paulsboro, Monroe Trainer, etc.)."))

# PADD2 residual feeders (~2,200 kbd, fed by Cushing/Patoka/Clearbrook)
NEW_EDGES.append(edge("patoka_hub", "ref_padd2_residual", "terminal_connector",
                      capacity_bpd=1_000_000, transit_time_days=1,
                      notes="Mid-Valley, Marathon Detroit Pipeline, BP Mokena spurs to PADD2 residual refineries."))
NEW_EDGES.append(edge("cushing_hub", "ref_padd2_residual", "terminal_connector",
                      capacity_bpd=800_000, transit_time_days=1,
                      notes="Phillips 66 Wood River system, Sun Pipeline takeoffs to other PADD2 refineries (Robinson, Borger via local connectors)."))
NEW_EDGES.append(edge("clearbrook_entry", "ref_padd2_residual", "terminal_connector",
                      capacity_bpd=500_000, transit_time_days=2,
                      notes="Canadian heavy via Enbridge Mainline laterals (Mokena, Superior WI, etc.) to PADD2 residual refineries."))

# PADD3 residual feeders (~6,700 kbd, the heavy lift — fed by all four Gulf hubs + offshore + foreign imports)
NEW_EDGES.append(edge("houston_hub", "ref_padd3_residual", "terminal_connector",
                      capacity_bpd=1_500_000, transit_time_days=0.5,
                      notes="Houston-area pipelines feed multiple residual refineries (Sweeny, Phillips 66 area, etc.)."))
NEW_EDGES.append(edge("nederland_hub", "ref_padd3_residual", "terminal_connector",
                      capacity_bpd=1_000_000, transit_time_days=0.5,
                      notes="Sun Pipeline, Sunoco Logistics local takeoffs to Beaumont/Port Arthur residual refineries (Valero PA, Total PA, ExxonMobil Beaumont)."))
NEW_EDGES.append(edge("corpus_christi_hub", "ref_padd3_residual", "terminal_connector",
                      capacity_bpd=1_200_000, transit_time_days=0.5,
                      notes="Local Corpus Christi pipelines to Valero, Citgo, Flint Hills Corpus refineries (~840 kbd combined)."))
NEW_EDGES.append(edge("st_james_hub", "ref_padd3_residual", "terminal_connector",
                      capacity_bpd=800_000, transit_time_days=0.5,
                      notes="Capline-reversed and Mississippi River refineries (Phillips 66 Belle Chasse, Marathon Detroit, etc.)."))
NEW_EDGES.append(edge("gulf_of_america", "ref_padd3_residual", "offshore_outflow",
                      capacity_bpd=600_000, transit_time_days=2,
                      notes="Direct offshore pipelines to Gulf refineries (Texas City, Pascagoula, etc.) bypassing main hubs."))
NEW_EDGES.append(edge("padd3_imports_agg", "ref_padd3_residual", "shipping_route",
                      vessel_class="VLCC_via_LOOP_plus_Aframax", transit_time_days=15,
                      capacity_bpd=2_000_000,
                      notes="Foreign tanker imports (Mexican Maya, Saudi, Iraqi, Brazilian heavy/medium sour) to Gulf Coast residual refineries."))

# PADD3 imports also reach existing named heavy-slate refineries
NEW_EDGES.append(edge("padd3_imports_agg", "ref_motiva_port_arthur", "shipping_route",
                      vessel_class="VLCC_via_LOOP_plus_Aframax", transit_time_days=15,
                      capacity_bpd=300_000,
                      notes="Foreign heavy/medium sour tanker imports to Motiva Port Arthur."))
NEW_EDGES.append(edge("padd3_imports_agg", "ref_citgo_lake_charles", "shipping_route",
                      vessel_class="Aframax_Suezmax", transit_time_days=12,
                      capacity_bpd=300_000,
                      notes="Foreign heavy crude (historically Venezuelan) to CITGO Lake Charles via tanker."))

# PADD4 residual feeders (~290 kbd, fed by local hubs + Wyoming/Colorado + Guernsey)
NEW_EDGES.append(edge("guernsey_hub", "ref_padd4_residual", "terminal_connector",
                      capacity_bpd=150_000, transit_time_days=1,
                      notes="Frontier pipeline takeoffs from Guernsey to Billings-area refineries (ExxonMobil, P66 Billings)."))
NEW_EDGES.append(edge("wyoming_conventional", "ref_padd4_residual", "production_outflow",
                      capacity_bpd=100_000, transit_time_days=1,
                      notes="Local WY production to Cheyenne / smaller Rockies refineries."))
NEW_EDGES.append(edge("colorado_conventional", "ref_padd4_residual", "production_outflow",
                      capacity_bpd=50_000, transit_time_days=1,
                      notes="Local DJ Basin production to PADD4 residual refining beyond Suncor Commerce City."))

# PADD5 residual feeders (~1,700 kbd, fed by imports + local CA + ANS tankers)
NEW_EDGES.append(edge("padd5_imports_agg", "ref_padd5_residual", "shipping_route",
                      vessel_class="VLCC", transit_time_days=15, capacity_bpd=1_000_000,
                      notes="Foreign tanker imports + Canadian via TMX (not modelled) feeding PADD5 residual refineries (P66 Wilmington, Rodeo, Valero Benicia, Marathon Anacortes, etc.)."))
NEW_EDGES.append(edge("valdez_origin", "ref_padd5_residual", "shipping_route",
                      vessel_class="Aframax", transit_time_days=7, capacity_bpd=200_000,
                      notes="ANS tankers to PADD5 residual West Coast refineries (Valero Benicia, P66 Ferndale, etc.) beyond the named LA / Bay Area / Cherry Point destinations."))
NEW_EDGES.append(edge("california_conventional", "ref_padd5_residual", "production_outflow",
                      capacity_bpd=150_000, transit_time_days=0.5,
                      notes="Local CA production to PADD5 residual refining (P66 Wilmington, ExxonMobil Torrance, Kern Energy, etc.) beyond Marathon LA / Chevron Richmond."))

# ---------------------------------------------------------------------------
# 3. Update padd*_refining_view rollups: add residual to children, update capacity
# ---------------------------------------------------------------------------
PADD_VIEW_UPDATES = {
    "padd1_refining_view": ("ref_padd1_residual",   848_000),
    "padd2_refining_view": ("ref_padd2_residual", 3_733_000),
    "padd3_refining_view": ("ref_padd3_residual", 9_496_000),
    "padd4_refining_view": ("ref_padd4_residual",   648_000),
    "padd5_refining_view": ("ref_padd5_residual", 2_559_000),
}
for view_id, (residual_id, new_cap) in PADD_VIEW_UPDATES.items():
    for n in G["nodes"]:
        if n["id"] == view_id:
            children = n["resolution_hierarchy"].setdefault("children", [])
            if residual_id not in children:
                children.append(residual_id)
            n["configuration"]["regional_capacity_bd"] = new_cap
            break

# ---------------------------------------------------------------------------
# Apply (idempotent)
# ---------------------------------------------------------------------------
existing_node_ids = {n["id"] for n in G["nodes"]}
existing_edge_ids = {e["id"] for e in G["edges"]}

added_nodes, added_edges = [], []
for n in NEW_NODES:
    if n["id"] not in existing_node_ids:
        G["nodes"].append(n)
        added_nodes.append(n["id"])

for e in NEW_EDGES:
    if e["id"] not in existing_edge_ids:
        G["edges"].append(e)
        added_edges.append(e["id"])

# Refresh meta counts
G["meta"]["node_count"] = len(G["nodes"])
G["meta"]["edge_count"] = len(G["edges"])
G["meta"]["node_subtype_counts"] = dict(Counter(n["node_subtype"] for n in G["nodes"]))
G["meta"]["edge_type_counts"]    = dict(Counter(e["edge_type"]    for e in G["edges"]))

JSON_PATH.write_text(json.dumps(G, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"Added {len(added_nodes)} nodes:")
for x in added_nodes:
    print(f"  + {x}")
print(f"\nAdded {len(added_edges)} edges:")
for x in added_edges:
    print(f"  + {x}")

print(f"\n=== Final state ===")
print(f"Nodes: {len(G['nodes'])}")
print(f"Edges: {len(G['edges'])}")
print(f"\nNode subtype counts:")
for k, v in sorted(G["meta"]["node_subtype_counts"].items()):
    print(f"  {k:30s} {v}")
print(f"\nEdge type counts:")
for k, v in sorted(G["meta"]["edge_type_counts"].items()):
    print(f"  {k:30s} {v}")
