"""
Refactor: introduce region-aggregate nodes carrying a complete set of mass-balance
variables (P, C, S, B, plus relational flows to boundary and sibling regions).

Each region aggregate replaces a cluster of one-observation-per-view nodes that
duplicated the same region at different variable types. After this patch + the
companion assign_eia / assign_formulas updates, region-level observations land
on these region aggregates and mass balance closes at every aggregate via a
single declarative formula on that node's balancing_item variable.

New nodes added:
  * usa_view, padd1_view, padd2_view, padd3_view, padd4_view, padd5_view
    -- abstract aggregate; owns the physical assets in the region (basins,
       refineries, hubs, SPR sites). Variables:
         production, consumption, inventory, balancing_item   (non-relational)
         plus inflow/outflow to foreign boundary + sibling regions             (relational, added below as edges)
  * foreign_supply
    -- abstract boundary node for non-Canadian foreign crude imports.
       Companion to canadian_oil_sands (already present, foreign production)
       and foreign_export_destination (already present, foreign sink).

New edges added (flow links, generate inflow/outflow variables on load):
  * (foreign_supply -> region) inflow per region                       (6 edges)
  * (canadian_oil_sands -> region) inflow per region                   (6 edges)
  * (region -> foreign_export_destination) outflow per region          (6 edges)
  * Inter-PADD: usa_view aggregates over PADDs (no edge — pure aggregation)
  * Inter-PADD between region aggregates (12 edges, mirroring the existing
    abstract inter-PADD edges between padd*_production_view / padd*_refining_view;
    those are kept until the migration completes).

After the assign_* updates, the per-observation view nodes
(padd*_production_view, padd*_refining_view, padd*_stocks_view,
padd*_imports_view, padd*_exports_view, padd*_canadian_inflow_view, and the
8 usa_*_view nodes) become redundant and will be retired by
drop_old_observation_views.py. Keeping the existing 12 abstract inter-PADD
edges intact for now to avoid breaking running views.

Idempotent: re-running converges. No backup written (patch is additive at this
stage; node_type defaults make re-runs safe).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
GRAPH = ROOT / "asset_graph" / "asset_graph.json"

REGION_NODES = [
    ("usa_view",   "USA crude balance aggregate",            "national"),
    ("padd1_view", "PADD 1 (East Coast) crude balance",      "padd"),
    ("padd2_view", "PADD 2 (Midwest) crude balance",         "padd"),
    ("padd3_view", "PADD 3 (Gulf Coast) crude balance",      "padd"),
    ("padd4_view", "PADD 4 (Rocky Mountain) crude balance",  "padd"),
    ("padd5_view", "PADD 5 (West Coast) crude balance",      "padd"),
]

BOUNDARY_NODE = {
    "id": "foreign_supply",
    "name": "Foreign crude supply (non-Canadian, aggregate)",
    "node_class": "observational",
    "node_subtype": "foreign_production_aggregate",
    "kind": "abstract",
    "starter_status": "authoritative",
    "attributes": {
        "geography": {"country": "non-USA", "scope": "national", "lat": None, "lon": None},
        "configuration": {"is_view": True, "boundary_kind": "foreign_supply"},
        "notes": (
            "Abstract boundary node representing non-Canadian foreign crude "
            "imports into the US. Companion to canadian_oil_sands (Canada) "
            "and foreign_export_destination (export sink). Total foreign "
            "supply = canadian_oil_sands + foreign_supply outflows."
        ),
    },
}


def make_region(asset_id: str, name: str, scope_tag: str) -> dict:
    return {
        "id": asset_id,
        "name": name,
        "node_class": "observational",
        "node_subtype": "region_view",
        "kind": "abstract",
        "starter_status": "authoritative",
        "attributes": {
            "geography": {"country": "USA", "scope": scope_tag, "lat": None, "lon": None},
            "configuration": {"is_view": True, "is_balance_region": True},
            "notes": (
                "Region balance aggregate. Carries production, consumption, "
                "inventory, balancing_item plus relational inflow/outflow to "
                "foreign boundary nodes and sibling regions. Variables aggregate "
                "from owned physical assets (basins, refineries, hubs, SPR sites) "
                "via sum_over_children, OR are TS-bound directly if region-level "
                "data is authoritative."
            ),
        },
    }


def make_flow_edge(src: str, tgt: str, mode_label: str, notes: str) -> dict:
    return {
        "id": f"e_{src}__{tgt}__{mode_label}",
        "source": src,
        "target": tgt,
        "edge_type": mode_label,
        "attributes": {
            "abstract_layer": True,
            "notes": notes,
            "mode": mode_label,
        },
    }


REGIONS = ["usa_view", "padd1_view", "padd2_view", "padd3_view", "padd4_view", "padd5_view"]

# Cross-boundary flow edges. Each generates one inflow + one outflow variable on load.
BOUNDARY_EDGES = []
for r in REGIONS:
    BOUNDARY_EDGES.append(("foreign_supply", r, "foreign_inflow",
                           f"Non-Canadian foreign imports into {r}"))
    BOUNDARY_EDGES.append(("canadian_oil_sands", r, "canadian_inflow",
                           f"Canadian crude imports into {r}"))
    BOUNDARY_EDGES.append((r, "foreign_export_destination", "foreign_outflow",
                           f"Crude exports from {r} to foreign destinations"))

# Inter-PADD edges between region aggregates (mirror the existing 12 abstract
# inter-PADD flow edges between padd*_production_view / padd*_refining_view,
# but at the new region-aggregate layer).
INTER_PADD_REGION_EDGES = [
    ("padd1_view", "padd2_view"), ("padd1_view", "padd3_view"),
    ("padd2_view", "padd1_view"), ("padd2_view", "padd3_view"), ("padd2_view", "padd4_view"),
    ("padd3_view", "padd1_view"), ("padd3_view", "padd2_view"), ("padd3_view", "padd4_view"),
    ("padd3_view", "padd5_view"),
    ("padd4_view", "padd2_view"), ("padd4_view", "padd3_view"),
    ("padd5_view", "padd3_view"),
]


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
    print(f"Reading {GRAPH}")
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    n0, e0 = len(graph["nodes"]), len(graph["edges"])

    # Region aggregates (6)
    for asset_id, name, scope in REGION_NODES:
        upsert_node(graph, make_region(asset_id, name, scope))
    # Foreign supply boundary (1)
    upsert_node(graph, BOUNDARY_NODE)

    # Cross-boundary edges (3 per region * 6 regions = 18)
    for src, tgt, mode, notes in BOUNDARY_EDGES:
        upsert_edge(graph, make_flow_edge(src, tgt, mode, notes))

    # Inter-PADD edges between region aggregates (12)
    for src, tgt in INTER_PADD_REGION_EDGES:
        upsert_edge(graph, make_flow_edge(src, tgt, "inter_padd_region_flow",
                                          f"{src} -> {tgt} aggregate inter-PADD flow"))

    n1, e1 = len(graph["nodes"]), len(graph["edges"])
    print(f"  nodes: {n0} -> {n1}  (+{n1-n0})")
    print(f"  edges: {e0} -> {e1}  (+{e1-e0})")

    tmp = GRAPH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(GRAPH)
    print(f"Saved {GRAPH}")


if __name__ == "__main__":
    main()
