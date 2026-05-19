"""Add 3 flow edges from foreign_supply to the physical import-terminal aggregates.

The padd[1,3,5]_imports_agg nodes are physical import-terminal aggregates
(node_subtype = import_terminal). They represent the set of foreign-tanker
import terminals in their PADD. Before this patch, their foreign-import value
was carried in the `production` slot via an alias formula -- semantically
wrong (an import terminal does not produce crude).

This patch adds the missing inflow edges so the value can live properly in
the `inflow` slot:

  foreign_supply -> padd1_imports_agg    (non-Canadian foreign tanker imports landing in PADD 1)
  foreign_supply -> padd3_imports_agg    (same, PADD 3)
  foreign_supply -> padd5_imports_agg    (same, PADD 5)

Idempotent: re-running converges. No backup written (this is purely additive at
the edges level — node count is unchanged, edge count goes up by 3).
"""
from __future__ import annotations
import json
from pathlib import Path

GRAPH = Path(__file__).parent / "asset_graph" / "asset_graph.json"

NEW_EDGES = [
    ("foreign_supply", "padd1_imports_agg",
     "Non-Canadian foreign tanker imports landing at PADD 1 import-terminal aggregate"),
    ("foreign_supply", "padd3_imports_agg",
     "Non-Canadian foreign tanker imports landing at PADD 3 import-terminal aggregate"),
    ("foreign_supply", "padd5_imports_agg",
     "Non-Canadian foreign tanker imports landing at PADD 5 import-terminal aggregate"),
]


def make_edge(src: str, tgt: str, notes: str) -> dict:
    return {
        "id":        f"e_{src}__{tgt}__foreign_inflow",
        "source":    src,
        "target":    tgt,
        "edge_type": "foreign_inflow",
        "attributes": {
            "abstract_layer": False,
            "notes": notes,
            "mode": "tanker",
        },
    }


def upsert_edge(graph: dict, edge: dict) -> str:
    by_id = {e["id"]: i for i, e in enumerate(graph["edges"])}
    if edge["id"] in by_id:
        graph["edges"][by_id[edge["id"]]] = edge
        return "updated"
    graph["edges"].append(edge)
    return "added"


def main() -> None:
    print(f"Reading {GRAPH}")
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    e0 = len(graph["edges"])

    counts = {"added": 0, "updated": 0}
    for src, tgt, notes in NEW_EDGES:
        action = upsert_edge(graph, make_edge(src, tgt, notes))
        counts[action] = counts.get(action, 0) + 1

    e1 = len(graph["edges"])
    print(f"  edges: {e0} -> {e1}  (+{e1-e0})")
    print(f"  patch: {counts}")

    tmp = GRAPH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(GRAPH)
    print(f"Saved {GRAPH}")


if __name__ == "__main__":
    main()
