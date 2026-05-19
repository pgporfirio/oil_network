"""Drop the now-redundant per-observation view nodes and rest_of_l48 from the
asset graph JSON. After the region-aggregate refactor (2026-05-11), these nodes
hold no TS bindings -- their observations have been retargeted to usa_view and
padd1..5_view, which carry the full mass-balance variable set.

Nodes retired:
  * USA per-observation (7):  usa_production_view, usa_refining_view,
    usa_imports_view, usa_exports_view, usa_canada_inflow_view,
    usa_total_stocks_view, usa_commercial_stocks_view
  * PADD per-observation (28):  padd[1..5]_production_view,
    padd[1..5]_refining_view, padd[1..5]_stocks_view,
    padd[1..5]_imports_view, padd[1..5]_exports_view,
    padd[1..3]_canadian_inflow_view (PADDs 4/5 have no Canadian-only series)
  * Pure-rollup orphan (1):  rest_of_l48 (no observation; redundant with PADD residuals)

Edges removed: every edge involving a retired node. Specifically the 12 abstract
inter-PADD edges between padd*_production_view / padd*_refining_view (their
bindings have been migrated to padd*_view <-> padd*_view edges added by
add_region_aggregates.py).

Idempotent: re-running converges. Backs up the JSON before writing.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
GRAPH = ROOT / "asset_graph" / "asset_graph.json"
BACKUP = GRAPH.with_name(f"asset_graph.backup_pre_drop_old_views_{datetime.now():%Y%m%d_%H%M%S}.json")

RETIRED_NODES = set([
    # USA per-observation
    "usa_production_view", "usa_refining_view",
    "usa_imports_view", "usa_exports_view", "usa_canada_inflow_view",
    "usa_total_stocks_view", "usa_commercial_stocks_view",
    # PADD per-observation
    "padd1_production_view", "padd2_production_view", "padd3_production_view",
    "padd4_production_view", "padd5_production_view",
    "padd1_refining_view", "padd2_refining_view", "padd3_refining_view",
    "padd4_refining_view", "padd5_refining_view",
    "padd1_stocks_view", "padd2_stocks_view", "padd3_stocks_view",
    "padd4_stocks_view", "padd5_stocks_view",
    "padd1_imports_view", "padd2_imports_view", "padd3_imports_view",
    "padd4_imports_view", "padd5_imports_view",
    "padd1_exports_view", "padd2_exports_view", "padd3_exports_view",
    "padd4_exports_view", "padd5_exports_view",
    "padd1_canadian_inflow_view", "padd2_canadian_inflow_view", "padd3_canadian_inflow_view",
    # Pure-rollup orphan
    "rest_of_l48",
])


def main() -> None:
    print(f"Reading {GRAPH}")
    raw = GRAPH.read_text(encoding="utf-8")
    BACKUP.write_text(raw, encoding="utf-8")
    print(f"backup -> {BACKUP.name}")

    graph = json.loads(raw)
    n0, e0 = len(graph["nodes"]), len(graph["edges"])

    # Drop nodes
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in RETIRED_NODES]
    n1 = len(graph["nodes"])

    # Drop edges involving any retired node (either endpoint)
    kept_edges = []
    dropped_edges = []
    for e in graph["edges"]:
        if e["source"] in RETIRED_NODES or e["target"] in RETIRED_NODES:
            dropped_edges.append(e["id"])
        else:
            kept_edges.append(e)
    graph["edges"] = kept_edges
    e1 = len(graph["edges"])

    # Update meta counts
    meta = graph.get("meta", {})
    meta["node_count"] = n1
    meta["edge_count"] = e1
    edge_type_counts = {}
    for e in graph["edges"]:
        et = e.get("edge_type", "unknown")
        edge_type_counts[et] = edge_type_counts.get(et, 0) + 1
    meta["edge_type_counts"] = edge_type_counts
    graph["meta"] = meta

    print(f"  nodes: {n0} -> {n1}  (-{n0-n1})")
    print(f"  edges: {e0} -> {e1}  (-{e0-e1})")
    print(f"  retired {len(RETIRED_NODES)} node IDs; dropped {len(dropped_edges)} edges")

    tmp = GRAPH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(GRAPH)
    print(f"Saved {GRAPH}")


if __name__ == "__main__":
    main()
