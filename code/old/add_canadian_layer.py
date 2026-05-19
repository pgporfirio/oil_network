"""
Pass 5: add the Canadian-crude inflow layer.

Today our model has clearbrook_entry as an `import_terminal` with no upstream
constraint, so the ~3 mb/d of Canadian Mainline crude that lands there is
implicit and untraceable. This pass makes Canadian flows explicit and adds the
three other major cross-border pipelines that aren't represented at all:

  - Keystone Pipeline (Hardisty AB -> Steele City NE -> Cushing OK, 620 kbd)
  - Trans Mountain TMX (Edmonton AB -> Burnaby BC -> tanker -> PADD5, 890 kbd)
  - Express + Platte (Hardisty AB -> Casper WY -> Wood River IL, 280 kbd)
  - Enbridge Mainline Canadian segment (Edmonton -> Clearbrook MN, 3,000 kbd)

Adds:
  - 1 production source: canadian_oil_sands (foreign aggregate of WCSB output:
    Alberta oil sands, Saskatchewan, BC; ~5,000 kbd capacity)
  - 4 pipeline nodes representing the cross-border corridors above
  - 8 flow edges (intake + outflow per pipeline)

Mass balance after this pass:
  - clearbrook_entry now has explicit upstream (canadian_oil_sands -> Mainline
    Canadian -> Clearbrook), so it is no longer a free source.
  - Canadian heavy reaching Cushing has a traceable origin (Keystone).
  - Canadian crude in PADD5 imports has a traceable Canadian portion (TMX);
    the rest of padd5_imports_agg is genuinely foreign tanker imports.
  - Canadian heavy reaching Guernsey has a traceable origin (Express+Platte);
    onward routing via Pony Express -> Cushing already exists.

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
NEW_NODES = []

# Canadian oil sands aggregate (production source, abstract / aggregated for now;
# could be disaggregated into Alberta oil sands / Saskatchewan / BC + smaller
# basins when finer Canadian data is needed).
NEW_NODES.append({
    "id": "canadian_oil_sands",
    "name": "Canadian Western Canadian Sedimentary Basin (aggregate)",
    "node_class": "production",
    "node_subtype": "foreign_production_aggregate",
    "geography": {
        "lat": 52.61, "lon": -111.34,    # Hardisty AB — major crude hub origin
        "county": None, "state": "AB",
        "padd": None,                     # not in any US PADD
        "country": "CA",
    },
    "resolution_hierarchy": {"parent": None, "children": [], "aggregation": "sum"},
    "configuration": {
        "production_capacity_bd": 5_000_000,
        "production_2023_2024_bd": 4_900_000,
        "basin_aggregation": [
            "Alberta oil sands (Athabasca, Cold Lake, Peace River) ~3,300 kbd",
            "Alberta + Saskatchewan conventional + tight ~1,200 kbd",
            "BC offshore + onshore ~150 kbd",
            "smaller (Manitoba, NWT) ~50 kbd",
        ],
        "primary_grades": [
            "Western Canadian Select (WCS) — heavy sour bitumen blend",
            "Cold Lake Blend (CLB) — heavy sour",
            "Mixed Sweet Blend (MSW) — light sweet",
            "Syncrude Synthetic Crude (SSX) — light sweet upgraded",
        ],
        "primary_export_corridors": [
            "Enbridge Mainline -> Clearbrook MN (US)",
            "Keystone -> Cushing OK (US)",
            "Trans Mountain TMX -> Burnaby BC -> tanker to PADD5 / Asia",
            "Express + Platte -> Wood River IL (US)",
        ],
        "operator": "multiple Canadian E&P (Suncor, CNRL, Cenovus, Imperial Oil, MEG, etc.)",
    },
    "starter_status": "authoritative",
    "notes": ("Foreign production aggregate. Position pinned to Hardisty AB, "
              "the canonical Canadian crude hub where the Mainline, Keystone, "
              "and Express systems originate. Canadian production exits via "
              "the four cross-border pipeline systems modelled here."),
})

# 4 cross-border pipelines.
def pipeline(node_id, name, intake_refs, outflow_refs, capacity_bpd,
             length_miles, transit_days, operator, country, notes,
             diameter_in=None, commission_year=None,
             grade_segregation="multi_grade_batches"):
    return {
        "id": node_id,
        "name": name,
        "node_class": "infrastructure",
        "node_subtype": "pipeline",
        "geography": {
            "lat": None, "lon": None, "county": None, "state": None,
            "padd": None, "country": country,
            "endpoint_intake_refs":  list(intake_refs),
            "endpoint_outflow_refs": list(outflow_refs),
            "spans_multiple_padds":  True,
        },
        "resolution_hierarchy": {"parent": None, "children": [], "aggregation": None},
        "configuration": {
            "operator": operator,
            "diameter_in": diameter_in,
            "length_miles": length_miles,
            "capacity_bpd": capacity_bpd,
            "transit_days": transit_days,
            "commissioning_year": commission_year,
            "grade_segregation": grade_segregation,
        },
        "starter_status": "authoritative",
        "notes": notes,
    }

NEW_NODES.append(pipeline(
    "pipe_enbridge_mainline_ca",
    "Enbridge Mainline Canadian segment (Edmonton -> Clearbrook)",
    intake_refs=["canadian_oil_sands"], outflow_refs=["clearbrook_entry"],
    capacity_bpd=3_000_000, length_miles=1_100, transit_days=8,
    operator="Enbridge",
    country="CA-US",
    diameter_in=36, commission_year=1950,
    grade_segregation="heavy_sour_Canadian_plus_synbit_batches",
    notes=("Canadian segment of the Enbridge Mainline (Lines 1/2/3/4/13/65/67). "
           "Largest crude pipeline system in North America. Carries Western "
           "Canadian Select, Cold Lake Blend, and synbits from Edmonton/Hardisty "
           "down to the US border; continues as the existing pipe_enbridge_mainline "
           "node from Clearbrook MN onward."),
))

NEW_NODES.append(pipeline(
    "pipe_keystone",
    "Keystone Pipeline (Hardisty -> Cushing)",
    intake_refs=["canadian_oil_sands"], outflow_refs=["cushing_hub"],
    capacity_bpd=620_000, length_miles=2_147, transit_days=10,
    operator="South Bow (formerly TC Energy)",
    country="CA-US",
    diameter_in=30, commission_year=2010,
    grade_segregation="heavy_sour_Canadian",
    notes=("The original Keystone Pipeline (not the cancelled Keystone XL extension). "
           "Carries heavy sour Canadian crude from Hardisty AB through Steele City "
           "NE down to Cushing OK. From Cushing, crude flows southbound on Marketlink "
           "and Seaway."),
))

NEW_NODES.append(pipeline(
    "pipe_trans_mountain_tmx",
    "Trans Mountain Pipeline + TMX expansion (Edmonton -> Burnaby BC -> tanker)",
    intake_refs=["canadian_oil_sands"], outflow_refs=["padd5_imports_agg"],
    capacity_bpd=890_000, length_miles=715, transit_days=12,  # incl. tanker leg
    operator="Trans Mountain Corporation (Government of Canada)",
    country="CA",
    diameter_in=36, commission_year=1953,  # original; TMX expansion 2024
    grade_segregation="heavy_sour_Canadian_plus_light_sweet",
    notes=("Trans Mountain Pipeline (original + TMX expansion completed May 2024). "
           "Edmonton -> Burnaby BC marine terminal at Westridge. The model "
           "abstracts the BC -> PADD5 tanker leg into the same node; output "
           "lands at padd5_imports_agg which then feeds Cherry Point and other "
           "PADD5 refineries. The TMX expansion tripled nameplate capacity to "
           "~890 kbd."),
))

NEW_NODES.append(pipeline(
    "pipe_express_platte",
    "Express + Platte Pipeline (Hardisty -> Casper -> Wood River)",
    intake_refs=["canadian_oil_sands"], outflow_refs=["guernsey_hub"],
    capacity_bpd=280_000, length_miles=1_700, transit_days=12,
    operator="Pembina (Express) / Tallgrass (Platte)",
    country="CA-US",
    diameter_in=24, commission_year=1997,
    grade_segregation="heavy_sour_Canadian_plus_Wyoming_sweet",
    notes=("Express Pipeline runs Hardisty AB -> Casper WY (1,250 km, 280 kbd). "
           "Continues as Platte Pipeline -> Wood River IL. The model lands "
           "Canadian flow at Guernsey hub (close to Casper) so onward routing "
           "via Pony Express -> Cushing and Marketlink -> Nederland is captured "
           "by the existing pipeline network."),
))

# ---------------------------------------------------------------------------
# 2. New edges (8 flow edges; intake + outflow per pipeline)
# ---------------------------------------------------------------------------
def edge(src, tgt, edge_type, **attrs):
    return {
        "id": f"e_{src}__{tgt}__{edge_type}",
        "source": src, "target": tgt, "edge_type": edge_type,
        "attributes": attrs,
    }

PIPELINE_INTAKE = [
    ("canadian_oil_sands", "pipe_enbridge_mainline_ca", 3_000_000,
     "Canadian Mainline intake at Edmonton/Hardisty area."),
    ("canadian_oil_sands", "pipe_keystone",             620_000,
     "Keystone intake at Hardisty AB."),
    ("canadian_oil_sands", "pipe_trans_mountain_tmx",   890_000,
     "Trans Mountain intake at Edmonton."),
    ("canadian_oil_sands", "pipe_express_platte",       280_000,
     "Express + Platte intake at Hardisty AB."),
]

PIPELINE_OUTFLOW = [
    ("pipe_enbridge_mainline_ca", "clearbrook_entry",   3_000_000, 8,
     "Mainline Canadian segment outflow at Clearbrook MN, the US-side import entry."),
    ("pipe_keystone",             "cushing_hub",        620_000,   10,
     "Keystone outflow at Cushing tank farm via Steele City NE."),
    ("pipe_trans_mountain_tmx",   "padd5_imports_agg",  890_000,   12,
     "TMX outflow: Burnaby BC marine terminal -> tanker -> PADD5 ports (Cherry Point, Anacortes, LA basin)."),
    ("pipe_express_platte",       "guernsey_hub",       280_000,   12,
     "Express + Platte outflow lands at Guernsey hub area; from there Pony Express carries crude south to Cushing."),
]

NEW_EDGES = []
for src, tgt, cap, note in PIPELINE_INTAKE:
    NEW_EDGES.append(edge(src, tgt, "pipeline_intake",
                          capacity_bpd=cap, transit_days=0.0, notes=note))
for src, tgt, cap, days, note in PIPELINE_OUTFLOW:
    NEW_EDGES.append(edge(src, tgt, "pipeline_outflow",
                          capacity_bpd=cap, transit_days=days, direction=None, notes=note))

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
    print(f"  {k:35s} {v}")
print(f"\nEdge type counts:")
for k, v in sorted(G["meta"]["edge_type_counts"].items()):
    print(f"  {k:30s} {v}")
