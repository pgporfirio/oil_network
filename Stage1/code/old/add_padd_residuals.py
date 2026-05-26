"""
Add PADD-level production residuals + a small set of observational aggregate
views, so every loaded EIA series can be TS-bound to a graph variable and the
modelled aggregations close the gap to EIA published totals.

What this adds:

1. **5 physical production residuals** (kind=physical, node_class=production,
   node_subtype=state_residual):
       padd1_other, padd2_other, padd3_other, padd4_other, padd5_other
   Each captures the unmodelled-state production within its PADD. Each has
   exactly one outflow:
       padd1_other -> padd1_refining_view  (no PADD 1 hub exists)
       padd2_other -> patoka_hub
       padd3_other -> houston_hub
       padd4_other -> guernsey_hub
       padd5_other -> padd5_refining_view  (no PADD 5 hub exists)

2. **Observational aggregate views** (kind=abstract, node_class=observational):
       usa_production_view              -- TS-bind to MCRFPUS2
       usa_refining_view                -- TS-bind to M_EPC0_YIY_NUS_2
       usa_canada_inflow_view           -- TS-bind to MCRIMUSCA2
       usa_lower48_excl_gom_view        -- TS-bind to PAPR48NGOM
       texas_state_view                 -- TS-bind to MCRFPTX2
       montana_state_view               -- TS-bind to MCRFPMT2
       padd1_canadian_inflow_view       -- TS-bind to MCRIPP1CA2
       padd2_canadian_inflow_view       -- TS-bind to MCRIPP2CA2
       padd3_canadian_inflow_view       -- TS-bind to MCRIPP3CA2

These observational nodes are pure views — they carry no flow edges
(per principle 2.6). They exist so every loaded EIA series binds to a graph
variable, and so aggregate consistency can be verified externally.

Idempotent: re-running after success is a no-op.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
GRAPH_PATH = ROOT / "asset_graph" / "asset_graph.json"


# -----------------------------------------------------------------------------
# 5 physical PADD-residual production nodes + their outflows
# -----------------------------------------------------------------------------
PADD_RESIDUALS = [
    {
        "asset_id": "padd1_other",
        "name": "PADD 1 production residual (NY/PA/WV/VA + small east coast)",
        "outflow_target": "padd1_refining_view",
        "padd": 1,
        "approx_kbd": 52,
        "examples": ["Pennsylvania conventional", "West Virginia", "New York", "Virginia", "Florida"],
    },
    {
        "asset_id": "padd2_other",
        "name": "PADD 2 production residual (KS/IL/IN/MI/KY/MO/NE/OH/SD/TN)",
        "outflow_target": "patoka_hub",
        "padd": 2,
        "approx_kbd": 220,
        "examples": ["Kansas", "Illinois", "Indiana", "Michigan", "Kentucky", "Missouri", "Nebraska", "Ohio", "South Dakota", "Tennessee"],
    },
    {
        "asset_id": "padd3_other",
        "name": "PADD 3 production residual (LA onshore + MS + AL + AR)",
        "outflow_target": "houston_hub",
        "padd": 3,
        "approx_kbd": 136,
        "examples": ["Louisiana onshore", "Mississippi", "Alabama", "Arkansas"],
    },
    {
        "asset_id": "padd4_other",
        "name": "PADD 4 production residual (UT + Niobrara residual + small)",
        "outflow_target": "guernsey_hub",
        "padd": 4,
        "approx_kbd": 183,
        "examples": ["Utah", "Idaho", "Niobrara residual non-Wyoming non-Colorado"],
    },
    {
        "asset_id": "padd5_other",
        "name": "PADD 5 production residual (NV/AZ/OR + small)",
        "outflow_target": "padd5_refining_view",
        "padd": 5,
        "approx_kbd": 21,
        "examples": ["Nevada", "Arizona", "Oregon"],
    },
]

# -----------------------------------------------------------------------------
# Observational aggregate views (formula-only, no flow edges)
# -----------------------------------------------------------------------------
OBS_VIEWS = [
    # USA-level
    ("usa_production_view",         "USA total crude production view (MCRFPUS2)",          "usa_view",            "national"),
    ("usa_refining_view",           "USA total refinery crude inputs view (NUS_2)",        "usa_view",            "national"),
    ("usa_canada_inflow_view",      "USA total imports from Canada (MCRIMUSCA2)",          "usa_canadian_inflow_view", "national"),
    ("usa_lower48_excl_gom_view",   "USA Lower-48 excl GoM production view (PAPR48NGOM)",  "usa_subtotal_view",   "national"),

    # State aggregates referenced by Tier-1 residual formulas
    ("texas_state_view",            "Texas state crude production view (MCRFPTX2)",        "state_view",          "state"),
    ("montana_state_view",          "Montana state crude production view (MCRFPMT2)",      "state_view",          "state"),

    # PADD-level Canadian inflows (constrain Mainline-CA + Keystone jointly)
    ("padd1_canadian_inflow_view",  "PADD 1 imports from Canada (MCRIPP1CA2)",             "padd_canadian_inflow_view", "padd"),
    ("padd2_canadian_inflow_view",  "PADD 2 imports from Canada (MCRIPP2CA2)",             "padd_canadian_inflow_view", "padd"),
    ("padd3_canadian_inflow_view",  "PADD 3 imports from Canada (MCRIPP3CA2)",             "padd_canadian_inflow_view", "padd"),
]


def make_node_padd_residual(spec: dict) -> dict:
    return {
        "id": spec["asset_id"],
        "name": spec["name"],
        "node_class": "production",
        "node_subtype": "state_residual",
        "kind": "physical",
        "starter_status": "authoritative",
        "attributes": {
            "geography": {
                "country": "USA",
                "padd": spec["padd"],
                "lat": None,
                "lon": None,
            },
            "configuration": {
                "approx_capacity_bd": spec["approx_kbd"] * 1000,
                "examples": spec["examples"],
                "is_residual": True,
            },
            "notes": (
                f"Captures unmodelled production in PADD {spec['padd']} so the PADD "
                f"production aggregate sums to the EIA-published total. "
                f"Production formula: padd{spec['padd']}_production_view "
                f"minus the explicitly-modelled basins/states in PADD {spec['padd']}."
            ),
        },
    }


def make_node_observational(asset_id: str, name: str, subtype: str, scope_tag: str) -> dict:
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
                "Observational aggregate view — formula-only, no flow edges. "
                "TS-bound to its corresponding EIA series for ground-truth reporting."
            ),
        },
    }


def make_edge(source: str, target: str, kind: str, capacity: int | None, notes: str) -> dict:
    return {
        "id": f"e_{source}__{target}__{kind}",
        "source": source,
        "target": target,
        "edge_type": kind,
        "attributes": {
            "capacity_bpd": capacity,
            "transit_time_days": 1,
            "notes": notes,
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


def patch(graph: dict) -> dict:
    counts = {"nodes_added": 0, "nodes_updated": 0, "edges_added": 0, "edges_updated": 0}

    # 1. PADD residual physical production nodes + their outflow edges
    for spec in PADD_RESIDUALS:
        node = make_node_padd_residual(spec)
        action = upsert_node(graph, node)
        counts[f"nodes_{action}"] = counts.get(f"nodes_{action}", 0) + 1

        edge = make_edge(
            source=spec["asset_id"],
            target=spec["outflow_target"],
            kind="production_outflow",
            capacity=spec["approx_kbd"] * 1000,
            notes=(
                f"PADD {spec['padd']} residual production routed via "
                f"{spec['outflow_target']} (latent / single-outflow carry-through "
                f"under starter scope; per-pipeline allocation unobservable)."
            ),
        )
        action = upsert_edge(graph, edge)
        counts[f"edges_{action}"] = counts.get(f"edges_{action}", 0) + 1

    # 2. Observational aggregate views (no edges — formula-only views)
    for asset_id, name, subtype, scope_tag in OBS_VIEWS:
        node = make_node_observational(asset_id, name, subtype, scope_tag)
        action = upsert_node(graph, node)
        counts[f"nodes_{action}"] = counts.get(f"nodes_{action}", 0) + 1

    return counts


def main() -> None:
    print(f"Reading {GRAPH_PATH}")
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    n0, e0 = len(graph["nodes"]), len(graph["edges"])
    print(f"  before: {n0} nodes, {e0} edges")

    counts = patch(graph)
    print(f"  patch:  {counts}")

    n1, e1 = len(graph["nodes"]), len(graph["edges"])
    print(f"  after:  {n1} nodes, {e1} edges  (delta: +{n1-n0} nodes, +{e1-e0} edges)")

    # Atomic write
    tmp = GRAPH_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(GRAPH_PATH)
    print(f"Saved {GRAPH_PATH}")


if __name__ == "__main__":
    main()
