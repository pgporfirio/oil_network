"""
Cherry-picked fixes from external patch review (2026-04-29).

Fixes applied:
  1. Dedupe 4 exact-duplicate edge IDs (predate our work)
  2. Remove redundant alaska_north_slope -> ans_gathering aggregation edge
     (production_to_gathering edge already covers the relationship; aggregation
     is also semantically off because alaska_north_slope is itself a production
     node, not a basin aggregate)
  3. Wire 11 aggregation edges for padd*_production_view nodes which were
     orphaned (children=[], no edges) while every other observational
     aggregate had populated children + edges
  4. Populate padd*_production_view.resolution_hierarchy.children lists
     to match
  5. Update Eagle Ford coverage contract: remove eagle_ford_gathering from
     collapsed_below (it is a real intermediate gathering node, not an alias)

Skipped from external patch:
  - Removing bakken_nd_gathering -> clearbrook_entry. The patcher's
    justification ("Clearbrook is a Canadian-crude entry only") is
    incomplete: Clearbrook MN is also the injection point for the
    Enbridge North Dakota system (Bakken crude north to the Mainline).
    That edge represents a real-world Bakken-to-Midwest route.

Idempotent.
"""
import json
from collections import Counter
from pathlib import Path

JSON_PATH = Path("asset_graph/asset_graph.json")
G = json.loads(JSON_PATH.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# 1. Dedupe edges by ID (keep first occurrence)
# ---------------------------------------------------------------------------
seen_ids = set()
deduped = []
removed_dups = []
for e in G["edges"]:
    if e["id"] in seen_ids:
        removed_dups.append(e["id"])
        continue
    seen_ids.add(e["id"])
    deduped.append(e)
G["edges"] = deduped

# ---------------------------------------------------------------------------
# 2. Remove redundant alaska_north_slope -> ans_gathering aggregation edge
# ---------------------------------------------------------------------------
TO_REMOVE_IDS = {
    "e_alaska_north_slope__ans_gathering__aggregation",
}
before = len(G["edges"])
G["edges"] = [e for e in G["edges"] if e["id"] not in TO_REMOVE_IDS]
removed_redundant = before - len(G["edges"])

# ---------------------------------------------------------------------------
# 3 + 4. Wire padd*_production_view aggregation edges + children lists
# ---------------------------------------------------------------------------
# Allocation follows the external patch (operational EIA-reporting mapping):
#   bakken_mt rolls up under PADD2 via Bakken basin reporting,
#   permian_nm rolls up under PADD3 via Permian basin reporting,
# even though their geography.padd values are PADD4 in the raw metadata.
# This matches how EIA reports basin-level production.
PADD_VIEW_CHILDREN = {
    "padd1_production_view": [],
    "padd2_production_view": ["bakken_nd", "bakken_mt", "oklahoma_conventional"],
    "padd3_production_view": ["permian_tx", "permian_nm", "eagle_ford_tx", "gulf_of_america"],
    "padd4_production_view": ["wyoming_conventional", "colorado_conventional"],
    "padd5_production_view": ["california_conventional", "alaska_north_slope"],
}

def make_agg_edge(parent, child):
    return {
        "id": f"e_{parent}__{child}__aggregation",
        "source": parent,
        "target": child,
        "edge_type": "aggregation",
        "attributes": {
            "rollup_role": "padd_production_view",
            "note": "Derived rollup: production_view aggregates member production nodes.",
        },
    }

existing_edge_ids = {e["id"] for e in G["edges"]}
added_padd_edges = []
for parent, children in PADD_VIEW_CHILDREN.items():
    # Update the node's resolution_hierarchy.children
    for n in G["nodes"]:
        if n["id"] == parent:
            n["resolution_hierarchy"]["children"] = list(children)
            break
    # Add aggregation edges
    for child in children:
        edge = make_agg_edge(parent, child)
        if edge["id"] not in existing_edge_ids:
            G["edges"].append(edge)
            added_padd_edges.append(edge["id"])
            existing_edge_ids.add(edge["id"])

# ---------------------------------------------------------------------------
# 5. Eagle Ford contract clarification
# ---------------------------------------------------------------------------
contract = G["starter_coverage_contract"]
ef = contract["authoritative_levels"].get("eagle_ford_subsystem")
if ef is not None:
    ef["collapsed_below"] = []
    ef["notes"] = ("eagle_ford_tx is the authoritative production node; "
                   "eagle_ford_gathering is a real intermediate node in the "
                   "asset graph (analogous to permian_tx_gathering), not a "
                   "collapsed alias.")

# ---------------------------------------------------------------------------
# Refresh meta counts
# ---------------------------------------------------------------------------
G["meta"]["node_count"] = len(G["nodes"])
G["meta"]["edge_count"] = len(G["edges"])
G["meta"]["node_subtype_counts"] = dict(Counter(n["node_subtype"] for n in G["nodes"]))
G["meta"]["edge_type_counts"]    = dict(Counter(e["edge_type"]    for e in G["edges"]))

JSON_PATH.write_text(json.dumps(G, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print(f"=== Cleanup applied ===\n")
print(f"Deduped {len(removed_dups)} duplicate edge IDs:")
for d in removed_dups:
    print(f"  - {d}")
print(f"\nRemoved {removed_redundant} redundant edges:")
for d in TO_REMOVE_IDS:
    print(f"  - {d}")
print(f"\nAdded {len(added_padd_edges)} padd_production_view aggregation edges:")
for d in added_padd_edges:
    print(f"  + {d}")
print(f"\nUpdated children lists for 5 padd_production_view nodes")
print(f"Eagle Ford coverage contract clarified")

print(f"\n=== Final state ===")
print(f"Nodes: {len(G['nodes'])}")
print(f"Edges: {len(G['edges'])}")
print(f"\nEdge type counts:")
for k, v in sorted(G["meta"]["edge_type_counts"].items()):
    print(f"  {k:30s} {v}")

# Sanity: any remaining duplicate edge IDs?
remaining_dups = [k for k, v in Counter(e["id"] for e in G["edges"]).items() if v > 1]
print(f"\nRemaining duplicate edge IDs: {len(remaining_dups)}")
