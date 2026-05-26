"""
Redirect state-residual production outflows to physical hubs.

Current state (before this patch):
  texas_other   --outflow-->  padd3_refining_view  (abstract — violates principle 2.6)
  montana_other --outflow-->  padd4_refining_view  (abstract — violates principle 2.6)

After this patch:
  texas_other   --outflow-->  houston_hub          (PADD 3 storage hub)
  montana_other --outflow-->  guernsey_hub         (PADD 4 Rockies storage hub)

Why these hubs:
- houston_hub: catch-all for inland Texas conventional (East Texas, Granite Wash,
  Barnett, Permian non-Permian, etc.) — already routes downstream to PADD 3 refineries
  via the district-default mapping, so the residual will reach physical sinks.
- guernsey_hub: PADD 4 Rockies aggregator. Montana-other (Cedar Creek etc.) most
  naturally aggregates here before flowing to Billings or south to Wyoming refiners.

The new outflow variables are bound as latent() under the starter scope — the
per-route allocation isn't observable in EIA data.

This script is idempotent: re-running it after success is a no-op.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
GRAPH_PATH = ROOT / "asset_graph" / "asset_graph.json"

REDIRECTS = [
    # (source, old_target, new_target, capacity_bpd, notes)
    (
        "texas_other",
        "padd3_refining_view",
        "houston_hub",
        350_000,
        "East Texas / Granite Wash / Barnett residual volumes staged at Houston hub "
        "before routing to PADD 3 refineries via the district-default mapping. "
        "Latent under starter scope.",
    ),
    (
        "montana_other",
        "padd4_refining_view",
        "guernsey_hub",
        50_000,
        "Cedar Creek (eastern MT) residual volumes routed via Guernsey (Rockies "
        "aggregator) onward to PADD 4 refineries. Latent under starter scope.",
    ),
]


def edge_id(source: str, target: str, kind: str = "production_outflow") -> str:
    return f"e_{source}__{target}__{kind}"


def patch(graph: dict) -> tuple[int, int]:
    """Returns (n_redirected, n_already_done)."""
    edges = graph["edges"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Index edges by id for fast lookup/replace
    by_id = {e["id"]: i for i, e in enumerate(edges)}

    n_redirected = 0
    n_already_done = 0

    for source, old_target, new_target, capacity, notes in REDIRECTS:
        old_id = edge_id(source, old_target)
        new_id = edge_id(source, new_target)

        # Idempotency: if the new edge already exists and the old one is gone, skip
        if new_id in by_id and old_id not in by_id:
            n_already_done += 1
            continue

        if old_id not in by_id:
            raise RuntimeError(
                f"Expected edge {old_id} not found — graph may be in an unexpected state."
            )

        old_edge = edges[by_id[old_id]]
        new_edge = {
            "id": new_id,
            "source": source,
            "target": new_target,
            "edge_type": "production_outflow",
            "attributes": {
                "capacity_bpd": capacity,
                "transit_time_days": 1,
                "notes": notes,
                "redirected_from": old_target,
                "redirected_at": now,
                "previous_redirect": old_edge.get("attributes", {}).get(
                    "redirected_from"
                ),
            },
        }
        # Replace in place (preserves order)
        edges[by_id[old_id]] = new_edge
        del by_id[old_id]
        by_id[new_id] = edges.index(new_edge)
        n_redirected += 1
        print(f"  redirected {source}: {old_target} -> {new_target}")

    return n_redirected, n_already_done


def main() -> None:
    print(f"Reading {GRAPH_PATH}")
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    print(f"  {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")

    n_redirected, n_already_done = patch(graph)
    print(f"  redirected: {n_redirected}, already-redirected (no-op): {n_already_done}")

    if n_redirected:
        # Write atomically via temp file
        tmp = GRAPH_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(GRAPH_PATH)
        print(f"Saved {GRAPH_PATH}")
    else:
        print("No changes to write.")


if __name__ == "__main__":
    main()
