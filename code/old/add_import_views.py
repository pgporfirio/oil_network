"""
Add observational aggregate view nodes for crude oil imports.

Six new abstract observational nodes (formula-only, no flow edges per principle 2.6):

  usa_imports_view              - TS-bind to MCRIMUS2  (US total all-countries)
  padd1_imports_view            - TS-bind to MCRIPP12  (PADD 1 total imports)
  padd2_imports_view            - TS-bind to MCRIPP22
  padd3_imports_view            - TS-bind to MCRIPP32
  padd4_imports_view            - TS-bind to MCRIPP42
  padd5_imports_view            - TS-bind to MCRIPP52

These are pure observation views. They do NOT receive flows from other graph
nodes; they exist so the EIA series have a TS-bound graph variable, and so the
foreign-tanker import volumes can be derived as
  paddX_imports_view - paddX_canadian_inflow_view
which becomes the production formula on the physical padd1/3/5_imports_agg
nodes.

Idempotent: re-running after success is a no-op.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
GRAPH_PATH = ROOT / "asset_graph" / "asset_graph.json"

VIEW_NODES = [
    ("usa_imports_view",   "USA total crude imports view (MCRIMUS2)",   "usa_imports_view",  "national"),
    ("padd1_imports_view", "PADD 1 total crude imports (MCRIPP12)",     "padd_imports_view", "padd"),
    ("padd2_imports_view", "PADD 2 total crude imports (MCRIPP22)",     "padd_imports_view", "padd"),
    ("padd3_imports_view", "PADD 3 total crude imports (MCRIPP32)",     "padd_imports_view", "padd"),
    ("padd4_imports_view", "PADD 4 total crude imports (MCRIPP42)",     "padd_imports_view", "padd"),
    ("padd5_imports_view", "PADD 5 total crude imports (MCRIPP52)",     "padd_imports_view", "padd"),
]


def make_view(asset_id: str, name: str, subtype: str, scope_tag: str) -> dict:
    return {
        "id": asset_id,
        "name": name,
        "node_class": "observational",
        "node_subtype": subtype,
        "kind": "abstract",
        "starter_status": "authoritative",
        "attributes": {
            "geography": {"country": "USA", "scope": scope_tag, "lat": None, "lon": None},
            "configuration": {"is_view": True},
            "notes": (
                "Observational aggregate view of total crude imports (all countries) "
                "at the indicated geographic scope. TS-bound to the corresponding EIA "
                "series; carries no flow edges (principle 2.6)."
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


def main() -> None:
    print(f"Reading {GRAPH_PATH}")
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    n0 = len(graph["nodes"])
    print(f"  before: {n0} nodes")

    counts = {"added": 0, "updated": 0}
    for asset_id, name, subtype, scope_tag in VIEW_NODES:
        action = upsert_node(graph, make_view(asset_id, name, subtype, scope_tag))
        counts[action] = counts.get(action, 0) + 1

    n1 = len(graph["nodes"])
    print(f"  patch: {counts}")
    print(f"  after: {n1} nodes  (delta: +{n1-n0})")

    tmp = GRAPH_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(GRAPH_PATH)
    print(f"Saved {GRAPH_PATH}")


if __name__ == "__main__":
    main()
