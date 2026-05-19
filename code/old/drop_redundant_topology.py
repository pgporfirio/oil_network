"""
Patch: drop redundant topology from `asset_graph.json`.

After establishing `formula_inputs` on `variable_assignments` as the single source
of truth for the aggregation graph, both `assets.attributes.resolution_hierarchy.children`
and the 58 `aggregation` entries in the JSON edges array are redundant. They have
no consumers (the loader skipped aggregation edges anyway) and they introduce
disagreement risk (e.g., topology said `padd2_production_view → bakken_mt`, but
the formula correctly excludes bakken_mt because MT is in PADD 4).

This patch:
  1. Removes `children` from every asset's `resolution_hierarchy` block. Keeps
     `parent` (lightweight navigation aid) and `aggregation` (rule label) intact.
  2. Removes every edge with `edge_type = 'aggregation'` from the edges array.

Idempotent.
"""
import copy
import json
from datetime import datetime
from pathlib import Path

GRAPH = Path(__file__).parent / "asset_graph" / "asset_graph.json"
BACKUP = GRAPH.with_name(f"asset_graph.backup_pre_drop_topology_{datetime.now():%Y%m%d_%H%M%S}.json")


def drop_children(graph):
    """Remove `children` from each asset's resolution_hierarchy. Returns count modified."""
    n = 0
    for node in graph["nodes"]:
        rh = node.get("resolution_hierarchy")
        if rh and "children" in rh:
            del rh["children"]
            n += 1
    return n


def drop_aggregation_edges(graph):
    """Remove all edges with edge_type='aggregation'. Returns (kept, dropped)."""
    before = len(graph["edges"])
    graph["edges"] = [e for e in graph["edges"] if e.get("edge_type") != "aggregation"]
    return (len(graph["edges"]), before - len(graph["edges"]))


def recount_meta(graph):
    nodes = graph["nodes"]
    edges = graph["edges"]
    meta = graph["meta"]
    meta["node_count"] = len(nodes)
    meta["edge_count"] = len(edges)
    et = {}
    for e in edges:
        et[e["edge_type"]] = et.get(e["edge_type"], 0) + 1
    meta["edge_type_counts"] = et


def main():
    raw = GRAPH.read_text(encoding="utf-8")
    BACKUP.write_text(raw, encoding="utf-8")
    print(f"backup -> {BACKUP.name}")

    graph = json.loads(raw)
    before = (graph["meta"]["node_count"], graph["meta"]["edge_count"])

    n_children = drop_children(graph)
    print(f"  resolution_hierarchy.children removed from {n_children} assets")

    n_kept, n_dropped = drop_aggregation_edges(graph)
    print(f"  aggregation edges dropped: {n_dropped} (kept {n_kept} flow edges)")

    recount_meta(graph)
    after = (graph["meta"]["node_count"], graph["meta"]["edge_count"])
    print(f"  nodes: {before[0]} -> {after[0]}")
    print(f"  edges: {before[1]} -> {after[1]}")
    print(f"  edge_type_counts: {graph['meta']['edge_type_counts']}")

    GRAPH.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {GRAPH.name}")


if __name__ == "__main__":
    main()
