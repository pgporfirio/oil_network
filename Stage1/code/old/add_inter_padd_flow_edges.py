"""
Patch: add abstract-level inter-PADD flow edges (Option A from the design discussion).

Rationale: when a scope has only PADD-level data (no per-pipeline / per-basin
observability), it needs flow variables on abstract nodes to carry the observed
inter-PADD movements. The asset graph stores these edges as a permanent part of
the topology (per principle 2.2 — stable topology). Under fine scopes the
abstract-flow variables are zero/latent (the underlying physical edges carry
the volume); under coarse scopes they're TS-bound to EIA `movements_*` series
and the physical edges below become latent.

Mapping: source = paddA_production_view, target = paddB_refining_view. EIA
publishes 10 inter-PADD pipeline pairs (the `MCRMP*` series). We add one edge
per directed pipeline pair.

Idempotent.
"""
import json
from datetime import datetime
from pathlib import Path

GRAPH = Path(__file__).parent / "asset_graph" / "asset_graph.json"
BACKUP = GRAPH.with_name(f"asset_graph.backup_pre_inter_padd_{datetime.now():%Y%m%d_%H%M%S}.json")

# 10 directed PADD pairs published as inter-PADD pipeline movements by EIA.
# (source_padd, target_padd, capacity_bpd_estimate, notes)
INTER_PADD_PAIRS = [
    ("padd1", "padd2", 100_000,  "PADD-1 -> PADD-2 (small east-to-midwest pipeline movements)"),
    ("padd2", "padd1", 200_000,  "PADD-2 -> PADD-1 (Mainline east extensions; small)"),
    ("padd2", "padd3", 1_500_000,"PADD-2 -> PADD-3 (Cushing -> Gulf via Marketlink/Seaway/Capline; major)"),
    ("padd2", "padd4", 100_000,  "PADD-2 -> PADD-4 (small)"),
    ("padd3", "padd1", 800_000,  "PADD-3 -> PADD-1 (Gulf -> East Coast via Capline northbound)"),
    ("padd3", "padd2", 800_000,  "PADD-3 -> PADD-2 (Gulf -> Midwest via reverse Capline / Seaway-reverse)"),
    ("padd3", "padd4", 100_000,  "PADD-3 -> PADD-4 (small Rockies-bound)"),
    ("padd4", "padd2", 700_000,  "PADD-4 -> PADD-2 (Wyoming/Bakken east via Pony Express / Mainline)"),
    ("padd4", "padd3", 600_000,  "PADD-4 -> PADD-3 (Pony Express -> Cushing -> Gulf)"),
    ("padd1", "padd3",  50_000,  "PADD-1 -> PADD-3 (very small)"),
    # Jones Act WB->WC and WC->WB tanker/barge routes (no pipeline equivalent).
    # Added 2026-05-11 alongside binding MCRMTP3P51 + MCRMTP5P31.
    ("padd3", "padd5", 200_000,  "PADD-3 -> PADD-5 (Jones Act tanker/barge, Gulf -> West Coast)"),
    ("padd5", "padd3",  50_000,  "PADD-5 -> PADD-3 (tanker/barge, small reverse flow)"),
]


def make_edge(src_padd, tgt_padd, capacity, notes):
    src = f"{src_padd}_production_view"
    tgt = f"{tgt_padd}_refining_view"
    return {
        "id": f"e_{src}__{tgt}__inter_padd_flow",
        "source": src,
        "target": tgt,
        "edge_type": "inter_padd_flow",
        "attributes": {
            "capacity_bpd": capacity,
            "mode": "pipeline",
            "source_padd": src_padd.upper(),
            "target_padd": tgt_padd.upper(),
            "notes": notes,
            "abstract_layer": True,
            "binding_under_starter_scope": "unassigned (scope is fine; physical edges below carry the volume)",
        },
    }


def upsert_edge(graph, edge):
    existing_ids = [e["id"] for e in graph["edges"]]
    if edge["id"] in existing_ids:
        idx = existing_ids.index(edge["id"])
        graph["edges"][idx] = edge
        return "updated"
    graph["edges"].append(edge)
    return "added"


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

    for src, tgt, cap, notes in INTER_PADD_PAIRS:
        e = make_edge(src, tgt, cap, notes)
        action = upsert_edge(graph, e)
        print(f"  edge  {action:>7}: {e['id']}")

    recount_meta(graph)
    after = (graph["meta"]["node_count"], graph["meta"]["edge_count"])
    print(f"  nodes: {before[0]} -> {after[0]}")
    print(f"  edges: {before[1]} -> {after[1]}")
    print(f"  inter_padd_flow edges: {graph['meta']['edge_type_counts'].get('inter_padd_flow', 0)}")

    GRAPH.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {GRAPH.name}")


if __name__ == "__main__":
    main()
