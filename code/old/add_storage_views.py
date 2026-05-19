"""
Add observational aggregate view nodes for crude oil stocks (inventory).

Seven new abstract observational nodes (formula-only, no flow edges per
principle 2.6). Each pairs to an EIA stock-level series:

  usa_total_stocks_view        - TS-bind to MCRSTUS1  (US total ending stocks)
  usa_commercial_stocks_view   - TS-bind to MCESTUS1  (US commercial ending stocks)
  padd1_stocks_view            - TS-bind to MCRSTP11
  padd2_stocks_view            - TS-bind to MCRSTP21
  padd3_stocks_view            - TS-bind to MCRSTP31
  padd4_stocks_view            - TS-bind to MCRSTP41
  padd5_stocks_view            - TS-bind to MCRSTP51

Note that no separate `usa_spr_stocks_view` is created: the existing
`spr_total` observational aggregate plays that role and is rebound from
`sum_over_children` to TS-bound on `MCSSTUS1` directly via assign_eia.

Also note that `cushing_hub` already exists as a physical storage_terminal —
its `inventory` variable is rebound directly to `MCRST_YCUOK_1` in assign_eia
(no new node here). Cushing is the only EIA-tracked individual hub.

Stock observations are LEVELS (snapshots in MBBL), not rates. They are stored
with `unit='mbbl_level'` in `oil_network.timeseries` to distinguish them from
flow-rate series (`unit='kbd'`) and raw monthly-volume series (`unit='mbbl'`).
No per-row days conversion applies.

Idempotent: re-running after success is a no-op.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
GRAPH_PATH = ROOT / "asset_graph" / "asset_graph.json"

VIEW_NODES = [
    ("usa_total_stocks_view",
     "USA total crude ending stocks view (MCRSTUS1)",
     "usa_stocks_view", "national",
     "Total US crude oil ending stocks (commercial + SPR + Alaska in-transit)."),
    ("usa_commercial_stocks_view",
     "USA commercial crude ending stocks view (MCESTUS1)",
     "usa_stocks_view", "national",
     "Commercial US crude oil ending stocks (excludes SPR)."),
    ("padd1_stocks_view", "PADD 1 crude ending stocks view (MCRSTP11)",
     "padd_stocks_view", "padd",
     "PADD 1 (East Coast) crude oil ending stocks."),
    ("padd2_stocks_view", "PADD 2 crude ending stocks view (MCRSTP21)",
     "padd_stocks_view", "padd",
     "PADD 2 (Midwest) crude oil ending stocks (includes Cushing)."),
    ("padd3_stocks_view", "PADD 3 crude ending stocks view (MCRSTP31)",
     "padd_stocks_view", "padd",
     "PADD 3 (Gulf Coast) crude oil ending stocks."),
    ("padd4_stocks_view", "PADD 4 crude ending stocks view (MCRSTP41)",
     "padd_stocks_view", "padd",
     "PADD 4 (Rocky Mountain) crude oil ending stocks."),
    ("padd5_stocks_view", "PADD 5 crude ending stocks view (MCRSTP51)",
     "padd_stocks_view", "padd",
     "PADD 5 (West Coast) crude oil ending stocks."),
]


def make_view(asset_id: str, name: str, subtype: str, scope_tag: str, note: str) -> dict:
    return {
        "id": asset_id,
        "name": name,
        "node_class": "observational",
        "node_subtype": subtype,
        "kind": "abstract",
        "starter_status": "authoritative",
        "attributes": {
            "geography": {"country": "USA", "scope": scope_tag, "lat": None, "lon": None},
            "configuration": {"is_view": True, "observation_kind": "stock_level"},
            "notes": (
                note + " "
                "Observational aggregate view of crude oil ending stocks at the "
                "indicated geographic scope. TS-bound to the corresponding EIA "
                "MCRST/MCEST series; carries no flow edges (principle 2.6). "
                "Inventory is a level (MBBL snapshot), not a rate."
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
    for asset_id, name, subtype, scope_tag, note in VIEW_NODES:
        action = upsert_node(graph, make_view(asset_id, name, subtype, scope_tag, note))
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
