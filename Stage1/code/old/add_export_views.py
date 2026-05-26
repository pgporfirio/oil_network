"""
Add observational view nodes for crude oil exports + a virtual boundary
destination node so the 4 modelled PADD-3 export terminals have a graph
endpoint for their boundary outflow.

What this adds:

1. **6 abstract observational view nodes** (formula-only, no flow edges):
       usa_exports_view              -- TS-bind to MCREXUS2
       padd1_exports_view            -- TS-bind to MCREXP12
       padd2_exports_view            -- TS-bind to MCREXP22
       padd3_exports_view            -- TS-bind to MCREXP32
       padd4_exports_view            -- TS-bind to MCREXP42 (~0 in 2024)
       padd5_exports_view            -- TS-bind to MCREXP52

2. **1 boundary destination node** `foreign_export_destination` (abstract
   infrastructure node — the symmetric counterpart of `canadian_oil_sands`
   for outgoing flows). All US crude leaving the system flows here.

3. **4 outflow edges** from PADD-3 export terminals to the destination:
       ingleside_export    -> foreign_export_destination
       houston_export      -> foreign_export_destination
       nederland_export    -> foreign_export_destination
       loop_terminal       -> foreign_export_destination

Per-terminal outflow stays `latent()` (per-terminal split unobservable,
jointly summing to MCREXP32 by mass balance). The destination's `consumption`
mirrors `padd3_exports_view.production` (= MCREXP32 = total flowing through
modelled terminals; the small unmodelled PADD 1/2/4/5 exports stay as
view-only observations).

Idempotent.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
GRAPH_PATH = ROOT / "asset_graph" / "asset_graph.json"

EXPORT_VIEWS = [
    ("usa_exports_view",   "USA total crude exports view (MCREXUS2)",   "usa_exports_view",  "national"),
    ("padd1_exports_view", "PADD 1 total crude exports (MCREXP12)",     "padd_exports_view", "padd"),
    ("padd2_exports_view", "PADD 2 total crude exports (MCREXP22)",     "padd_exports_view", "padd"),
    ("padd3_exports_view", "PADD 3 total crude exports (MCREXP32)",     "padd_exports_view", "padd"),
    ("padd4_exports_view", "PADD 4 total crude exports (MCREXP42)",     "padd_exports_view", "padd"),
    ("padd5_exports_view", "PADD 5 total crude exports (MCREXP52)",     "padd_exports_view", "padd"),
]

# The 4 PADD-3 export terminals that flow through the boundary destination.
# Sub-terminals (echo_export_dock, houston_ship_channel_export, seabrook_export)
# are collapsed under houston_export per the starter scope, so they don't get
# their own outflow edges.
EXPORT_TERMINALS = [
    "ingleside_export",
    "houston_export",
    "nederland_export",
    "loop_terminal",
]

DESTINATION_ID = "foreign_export_destination"


def make_view_node(asset_id: str, name: str, subtype: str, scope: str) -> dict:
    return {
        "id": asset_id,
        "name": name,
        "node_class": "observational",
        "node_subtype": subtype,
        "kind": "abstract",
        "starter_status": "authoritative",
        "attributes": {
            "geography": {"country": "USA", "scope": scope, "lat": None, "lon": None},
            "configuration": {"is_view": True},
            "notes": (
                "Observational aggregate view of total crude exports at the indicated "
                "geographic scope. TS-bound to the corresponding EIA MCREXP series."
            ),
        },
    }


def make_destination() -> dict:
    return {
        "id": DESTINATION_ID,
        "name": "Foreign export destination (boundary sink)",
        "node_class": "infrastructure",
        "node_subtype": "foreign_export_destination",
        "kind": "abstract",
        "starter_status": "authoritative",
        "attributes": {
            "geography": {"country": None, "scope": "boundary", "lat": None, "lon": None},
            "configuration": {"is_boundary": True},
            "notes": (
                "Virtual boundary node representing the foreign destination of US crude "
                "exports. Symmetric counterpart of canadian_oil_sands (foreign source). "
                "Receives outflows from the 4 modelled PADD-3 export terminals; its "
                "consumption variable holds the modelled-PADD-3 export total "
                "(= MCREXP32, mirrored from padd3_exports_view)."
            ),
        },
    }


def make_edge(source: str, target: str) -> dict:
    return {
        "id": f"e_{source}__{target}__terminal_export",
        "source": source,
        "target": target,
        "edge_type": "terminal_export",
        "attributes": {
            "capacity_bpd": None,
            "transit_time_days": 1,
            "notes": (
                f"Crude exported via {source} leaves the US system at the boundary. "
                "Per-terminal flow is latent — jointly constrained by MCREXP32 across "
                "the 4 modelled PADD-3 terminals."
            ),
        },
    }


def upsert_node(graph: dict, node: dict) -> str:
    by_id = {n["id"]: i for i, n in enumerate(graph["nodes"])}
    if node["id"] in by_id:
        graph["nodes"][by_id[node["id"]]] = node
        return "updated"
    graph["nodes"].append(node)
    return "added"


def upsert_edge(graph: dict, edge: dict) -> str:
    by_id = {e["id"]: i for i, e in enumerate(graph["edges"])}
    if edge["id"] in by_id:
        graph["edges"][by_id[edge["id"]]] = edge
        return "updated"
    graph["edges"].append(edge)
    return "added"


def main() -> None:
    print(f"Reading {GRAPH_PATH}")
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    n0, e0 = len(graph["nodes"]), len(graph["edges"])
    print(f"  before: {n0} nodes, {e0} edges")

    counts = {"nodes_added": 0, "nodes_updated": 0, "edges_added": 0, "edges_updated": 0}

    for asset_id, name, subtype, scope in EXPORT_VIEWS:
        action = upsert_node(graph, make_view_node(asset_id, name, subtype, scope))
        counts[f"nodes_{action}"] = counts.get(f"nodes_{action}", 0) + 1

    action = upsert_node(graph, make_destination())
    counts[f"nodes_{action}"] = counts.get(f"nodes_{action}", 0) + 1

    for term in EXPORT_TERMINALS:
        action = upsert_edge(graph, make_edge(term, DESTINATION_ID))
        counts[f"edges_{action}"] = counts.get(f"edges_{action}", 0) + 1

    print(f"  patch: {counts}")
    n1, e1 = len(graph["nodes"]), len(graph["edges"])
    print(f"  after: {n1} nodes (+{n1-n0}), {e1} edges (+{e1-e0})")

    tmp = GRAPH_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(GRAPH_PATH)
    print(f"Saved {GRAPH_PATH}")


if __name__ == "__main__":
    main()
