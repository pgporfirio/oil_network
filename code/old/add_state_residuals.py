"""
Patch: add state-level production residuals exposed by the basin/state EIA decomposition.

Context: with STEO basin-level + crpdn state-level data we can decompose Texas and
Montana into named-basin and residual portions:

    permian_tx       = COPRPM × 1000 − permian_nm.production
    eagle_ford_tx    = COPREF × 1000
    texas_other      = MCRFPTX2 − permian_tx − eagle_ford_tx     (~230 kbd, derived)

    bakken_nd        = MCRFPND2
    bakken_mt        = COPRBK × 1000 − bakken_nd
    montana_other    = MCRFPMT2 − bakken_mt                       (~21 kbd, derived)

ND ≈ Bakken-ND and NM ≈ Permian-NM by construction, so no `north_dakota_other`
or `nm_other` nodes are needed.

Adds two physical production nodes plus their primary outflow edges, and registers
both as children of the `rest_of_l48` aggregate. Idempotent: re-running is safe.
"""
import copy
import json
from datetime import datetime
from pathlib import Path

GRAPH = Path(__file__).parent / "asset_graph" / "asset_graph.json"
BACKUP = GRAPH.with_name(f"asset_graph.backup_pre_state_residuals_{datetime.now():%Y%m%d_%H%M%S}.json")


NEW_NODES = [
    {
        "id": "texas_other",
        "name": "Texas non-Permian non-Eagle-Ford residual (East TX, Granite Wash, Barnett)",
        "node_class": "production",
        "node_subtype": "state_conventional",
        "geography": {
            "lat": 32.0,
            "lon": -95.0,
            "county": None,
            "state": "TX",
            "padd": "PADD3",
            "country": "US",
        },
        "resolution_hierarchy": {
            "parent": "rest_of_l48",
            "children": [],
            "aggregation": "sum",
        },
        "configuration": {
            "production_type": "conventional_plus_tight",
            "notes": (
                "Residual = MCRFPTX2 − permian_tx − eagle_ford_tx. "
                "Captures East Texas conventional, Granite Wash (panhandle), Barnett "
                "Shale (DFW), plus minor Cotton Valley / Austin Chalk volumes. "
                "Typical magnitude ~230 kbd (≈4% of TX state total)."
            ),
        },
        "starter_status": "collapsed",
    },
    {
        "id": "montana_other",
        "name": "Montana non-Bakken residual (Cedar Creek)",
        "node_class": "production",
        "node_subtype": "state_conventional",
        "geography": {
            "lat": 46.5,
            "lon": -104.5,
            "county": None,
            "state": "MT",
            "padd": "PADD4",
            "country": "US",
        },
        "resolution_hierarchy": {
            "parent": "rest_of_l48",
            "children": [],
            "aggregation": "sum",
        },
        "configuration": {
            "production_type": "conventional",
            "notes": (
                "Residual = MCRFPMT2 − bakken_mt. Predominantly Cedar Creek anticline "
                "(eastern MT) plus minor non-Bakken Williston volumes. Typical magnitude "
                "~21 kbd."
            ),
        },
        "starter_status": "collapsed",
    },
]


NEW_EDGES = [
    {
        "id": "e_texas_other__ref_padd3_residual__production_outflow",
        "source": "texas_other",
        "target": "ref_padd3_residual",
        "edge_type": "production_outflow",
        "attributes": {
            "capacity_bpd": 350000,
            "transit_time_days": 1,
            "notes": (
                "East Texas / Granite Wash / Barnett residual volumes refined locally "
                "in PADD 3 (Beaumont, Pasadena, Houston periphery, smaller Tyler / "
                "Longview area refineries)."
            ),
        },
    },
    {
        "id": "e_montana_other__ref_padd4_residual__production_outflow",
        "source": "montana_other",
        "target": "ref_padd4_residual",
        "edge_type": "production_outflow",
        "attributes": {
            "capacity_bpd": 50000,
            "transit_time_days": 1,
            "notes": (
                "Cedar Creek (eastern MT) volumes refined in PADD 4 — primarily Billings "
                "(ExxonMobil, Phillips 66) and onward to Wyoming via the Cenex / "
                "Belle Fourche pipeline system."
            ),
        },
    },
]


def upsert_node(graph, new_node):
    existing_ids = [n["id"] for n in graph["nodes"]]
    if new_node["id"] in existing_ids:
        idx = existing_ids.index(new_node["id"])
        graph["nodes"][idx] = new_node
        return "updated"
    graph["nodes"].append(new_node)
    return "added"


def upsert_edge(graph, new_edge):
    existing_ids = [e["id"] for e in graph["edges"]]
    if new_edge["id"] in existing_ids:
        idx = existing_ids.index(new_edge["id"])
        graph["edges"][idx] = new_edge
        return "updated"
    graph["edges"].append(new_edge)
    return "added"


def add_to_rest_of_l48_children(graph, child_ids):
    parent = next(n for n in graph["nodes"] if n["id"] == "rest_of_l48")
    children = parent["resolution_hierarchy"]["children"]
    added = []
    for cid in child_ids:
        if cid not in children:
            children.append(cid)
            added.append(cid)
    return added


def recount_meta(graph):
    nodes = graph["nodes"]
    edges = graph["edges"]
    meta = graph["meta"]
    meta["node_count"] = len(nodes)
    meta["edge_count"] = len(edges)

    nc = {}
    for n in nodes:
        nc[n["node_class"]] = nc.get(n["node_class"], 0) + 1
    meta["node_class_counts"] = nc

    ns = {}
    for n in nodes:
        ns[n["node_subtype"]] = ns.get(n["node_subtype"], 0) + 1
    meta["node_subtype_counts"] = ns

    et = {}
    for e in edges:
        et[e["edge_type"]] = et.get(e["edge_type"], 0) + 1
    meta["edge_type_counts"] = et

    ss = {}
    for n in nodes:
        s = n.get("starter_status")
        if s:
            ss[s] = ss.get(s, 0) + 1
    meta["starter_status_counts"] = ss


def main():
    raw = GRAPH.read_text(encoding="utf-8")
    BACKUP.write_text(raw, encoding="utf-8")
    print(f"backup -> {BACKUP.name}")

    graph = json.loads(raw)
    before = (
        graph["meta"]["node_count"],
        graph["meta"]["edge_count"],
    )

    for node in NEW_NODES:
        action = upsert_node(graph, node)
        print(f"  node  {action:>7}: {node['id']}")

    for edge in NEW_EDGES:
        action = upsert_edge(graph, edge)
        print(f"  edge  {action:>7}: {edge['id']}")

    added_children = add_to_rest_of_l48_children(graph, [n["id"] for n in NEW_NODES])
    print(f"  rest_of_l48 children +{len(added_children)}: {added_children}")

    recount_meta(graph)
    after = (graph["meta"]["node_count"], graph["meta"]["edge_count"])
    print(f"  nodes: {before[0]} -> {after[0]}")
    print(f"  edges: {before[1]} -> {after[1]}")
    print(f"  state_conventional: {graph['meta']['node_subtype_counts'].get('state_conventional')}")
    print(f"  production: {graph['meta']['node_class_counts'].get('production')}")

    GRAPH.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {GRAPH.name}")


if __name__ == "__main__":
    main()
