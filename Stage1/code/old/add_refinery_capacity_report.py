"""
Patch: replace placeholder PADD-residual refineries with the full per-facility
list from EIA's annual Refinery Capacity Report (refcap25.xlsx), and add an
intermediate refining-district resolution layer between refineries and PADD
refining-views.

Three things happen:

1. **Drop 5 PADD residual refineries** (`ref_padd*_residual`). They were
   placeholders for unmodelled capacity; with ~126 refineries from the report
   loaded, residuals are obsolete. Inflow edges that previously fed each
   residual are redirected to the corresponding `padd*_refining_view` (an
   abstract-flow edge — same pattern as the inter-PADD edges).

2. **Add 10 refining-district aggregates** (`district_<duoarea>_refining_view`):
   REC, RAP (PADD 1); R2A, R2B, R2C (PADD 2); R3A, R3B, R3C, R3D, R3E (PADD 3).
   PADDs 4 and 5 are single-district so their existing `padd4_refining_view` /
   `padd5_refining_view` serve double-duty. Each district's parent in the
   resolution_hierarchy is its `padd*_refining_view`.

3. **Upsert refineries from the capacity report**. Manual EXISTING_MATCHES table
   maps the existing 19 named refineries to their report rows; matched refineries
   are updated in place (keeping their flow edges), unmatched report rows are
   added as brand-new physical nodes with `parent = district_<X>_refining_view`
   and no flow edges. The marathon_la and salt_lake_cluster nodes are treated as
   logical aggregates of multiple report sites; their constituent sites are
   skipped to avoid double-counting.

Idempotent.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT     = Path(__file__).parent
GRAPH    = ROOT / "asset_graph" / "asset_graph.json"
XLSX     = ROOT / "data" / "refcap25.xlsx"
BACKUP   = GRAPH.with_name(f"asset_graph.backup_pre_refcap_{datetime.now():%Y%m%d_%H%M%S}.json")


# Map district-label (RDIST_LABEL in xlsx) -> EIA duoarea code -> PADD
DISTRICT_INFO = {
    "East Coast":                                   {"duoarea": "REC", "padd": "PADD1"},
    "Appalachian No. 1":                            {"duoarea": "RAP", "padd": "PADD1"},
    "Indiana-Illinois-Kentucky":                    {"duoarea": "R2A", "padd": "PADD2"},
    "Minnesota-Wisconsin-North and South Dakota":   {"duoarea": "R2B", "padd": "PADD2"},
    "Oklahoma-Kansas-Missouri":                     {"duoarea": "R2C", "padd": "PADD2"},
    "Texas Inland":                                 {"duoarea": "R3A", "padd": "PADD3"},
    "Texas Gulf Coast":                             {"duoarea": "R3B", "padd": "PADD3"},
    "Louisiana Gulf Coast":                         {"duoarea": "R3C", "padd": "PADD3"},
    "North Louisiana-Arkansas":                     {"duoarea": "R3D", "padd": "PADD3"},
    "New Mexico":                                   {"duoarea": "R3E", "padd": "PADD3"},
    "Rocky Mountain":                               {"duoarea": "R40", "padd": "PADD4"},
    "West Coast":                                   {"duoarea": "R50", "padd": "PADD5"},
}

# 10 sub-PADD districts get their own aggregate node. PADDs 4 and 5 are
# single-district so we reuse the existing `padd4_refining_view` /
# `padd5_refining_view` instead of creating district_R40 / district_R50.
SUB_PADD_DUOAREAS = {"REC", "RAP", "R2A", "R2B", "R2C", "R3A", "R3B", "R3C", "R3D", "R3E"}


def parent_view_for_district(duoarea: str) -> str:
    """For a refinery's district, return the parent aggregate node id."""
    if duoarea in SUB_PADD_DUOAREAS:
        return f"district_{duoarea}_refining_view"
    if duoarea == "R40":
        return "padd4_refining_view"
    if duoarea == "R50":
        return "padd5_refining_view"
    raise ValueError(f"unknown duoarea {duoarea}")


# -----------------------------------------------------------------------------
# Manual matches: existing refinery node id -> list of (SITE, STATE_NAME) pairs
# in the capacity report. Multi-site entries are aggregates (marathon_la covers
# Carson + Wilmington; salt_lake_cluster covers SLC-area refineries).
# -----------------------------------------------------------------------------
EXISTING_MATCHES = {
    "ref_bp_cherry_point":        [("FERNDALE",       "Washington")],
    "ref_bp_whiting":             [("WHITING",        "Indiana")],
    "ref_chevron_richmond":       [("RICHMOND",       "California")],
    "ref_citgo_lake_charles":     [("LAKE CHARLES",   "Louisiana")],
    "ref_exxon_baytown":          [("BAYTOWN",        "Texas")],
    "ref_exxon_joliet":           [("JOLIET",         "Illinois")],
    "ref_galveston_bay_mpc":      [("TEXAS CITY",     "Texas")],
    "ref_hf_sinclair_wy":         [("SINCLAIR",       "Wyoming")],
    "ref_marathon_catlettsburg":  [("CATLETTSBURG",   "Kentucky")],
    "ref_marathon_garyville":     [("GARYVILLE",      "Louisiana")],
    "ref_marathon_la":            [("CARSON",         "California"), ("WILMINGTON", "California")],
    "ref_motiva_port_arthur":     [("PORT ARTHUR",    "Texas")],
    "ref_p66_bayway":             [("LINDEN",         "New Jersey")],
    "ref_p66_ponca_city":         [("PONCA CITY",     "Oklahoma")],
    "ref_p66_wood_river":         [("WOOD RIVER",     "Illinois")],
    "ref_pbf_delaware_city":      [("DELAWARE CITY",  "Delaware")],
    "ref_pbf_toledo":             [("TOLEDO",         "Ohio")],
    "ref_salt_lake_cluster":      [
        ("SALT LAKE CITY",   "Utah"),
        ("NORTH SALT LAKE",  "Utah"),
        ("WOODS CROSS",      "Utah"),
    ],
    "ref_suncor_commerce_city":   [("COMMERCE CITY EAST", "Colorado"), ("COMMERCE CITY WEST", "Colorado")],
}


PADD_RESIDUAL_REDIRECTS = {
    "ref_padd1_residual": "padd1_refining_view",
    "ref_padd2_residual": "padd2_refining_view",
    "ref_padd3_residual": "padd3_refining_view",
    "ref_padd4_residual": "padd4_refining_view",
    "ref_padd5_residual": "padd5_refining_view",
}


# -----------------------------------------------------------------------------
# Capacity report extraction
# -----------------------------------------------------------------------------
def load_capacity_report() -> pd.DataFrame:
    df = pd.read_excel(XLSX, header=0)
    df.columns = [c.strip().upper() for c in df.columns]
    cap = df[
        (df["PRODUCT"] == "TOTAL OPERABLE CAPACITY")
        & (df["SUPPLY"].str.contains("calendar day", case=False, na=False))
    ].copy()
    return cap.reset_index(drop=True)


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def operator_short(company_name: str) -> str:
    """Take the first meaningful word of COMPANY_NAME."""
    name = company_name.strip()
    skip = {"THE"}
    parts = [w for w in re.split(r"\W+", name) if w and w.upper() not in skip]
    return slugify(parts[0]) if parts else "unknown"


def make_new_refinery_id(row) -> str:
    op = operator_short(row["COMPANY_NAME"])
    site = slugify(row["SITE"])
    return f"ref_{op}_{site}"


def make_refinery_node(row, node_id, parent_view):
    """Build the JSON node dict for one refinery."""
    duoarea = DISTRICT_INFO[row["RDIST_LABEL"]]["duoarea"]
    padd    = DISTRICT_INFO[row["RDIST_LABEL"]]["padd"]
    return {
        "id": node_id,
        "name": f"{row['COMPANY_NAME']} — {row['SITE']}",
        "node_class": "infrastructure",
        "node_subtype": "refinery",
        "geography": {
            "lat": None, "lon": None, "county": None,
            "state": row["STATE_NAME"], "padd": padd, "country": "US",
        },
        "resolution_hierarchy": {
            "parent": parent_view,
            "aggregation": "sum",
        },
        "configuration": {
            "capacity_bpd": int(row["QUANTITY"]),
            "corporation": row["CORPORATION"],
            "company_name": row["COMPANY_NAME"],
            "site": row["SITE"],
            "rdist_label": row["RDIST_LABEL"],
            "duoarea_code": duoarea,
            "data_source": "EIA Refinery Capacity Report 2025 (refcap25.xlsx)",
        },
        "starter_status": "collapsed",
    }


def make_district_node(duoarea: str) -> dict:
    """Build one district aggregate node."""
    label = next(k for k, v in DISTRICT_INFO.items() if v["duoarea"] == duoarea)
    padd  = DISTRICT_INFO[label]["padd"]
    parent_padd_view = f"padd{padd[-1]}_refining_view"
    return {
        "id": f"district_{duoarea}_refining_view",
        "name": f"Refining District {duoarea} — {label} (refining view)",
        "node_class": "observational",
        "node_subtype": "refining_district_view",
        "geography": {
            "lat": None, "lon": None, "county": None,
            "state": None, "padd": padd, "country": "US",
        },
        "resolution_hierarchy": {
            "parent": parent_padd_view,
            "aggregation": "sum",
        },
        "configuration": {
            "duoarea_code": duoarea,
            "rdist_label": label,
            "data_source_typical": f"EIA refinery_input dataset, district series M_EPC0_YIY_{duoarea}_2",
        },
        "starter_status": "authoritative",
    }


# -----------------------------------------------------------------------------
# Apply patch to graph
# -----------------------------------------------------------------------------
def upsert_node(graph, node):
    ids = [n["id"] for n in graph["nodes"]]
    if node["id"] in ids:
        i = ids.index(node["id"])
        existing = graph["nodes"][i]
        # Merge: existing flow edges + IDs preserved; metadata refreshed from report.
        # `configuration` and `geography` are merged so existing values (lat/lon,
        # operator info, etc.) are preserved when the new dict contains None there.
        merged = {**existing, **node}
        merged["configuration"] = {**existing.get("configuration", {}), **node.get("configuration", {})}
        # For geography: keep existing values for any field where the new value is None.
        merged_geo = {**existing.get("geography", {})}
        for k, v in node.get("geography", {}).items():
            if v is not None:
                merged_geo[k] = v
        merged["geography"] = merged_geo
        graph["nodes"][i] = merged
        return "updated"
    graph["nodes"].append(node)
    return "added"


def upsert_edge(graph, edge):
    ids = [e["id"] for e in graph["edges"]]
    if edge["id"] in ids:
        i = ids.index(edge["id"])
        graph["edges"][i] = edge
        return "updated"
    graph["edges"].append(edge)
    return "added"


def drop_node_and_redirect_edges(graph, node_id, redirect_target):
    """Drop a node; redirect any edge with target=node_id to point at redirect_target."""
    n_redirected = 0
    for e in graph["edges"]:
        if e.get("target") == node_id:
            old_id = e["id"]
            e["target"] = redirect_target
            # rewrite edge id so it matches new target
            new_id = old_id.replace(f"__{node_id}__", f"__{redirect_target}__")
            e["id"] = new_id
            attrs = e.setdefault("attributes", {})
            attrs["redirected_from"] = node_id
            attrs["redirected_at"]   = datetime.now().isoformat(timespec="seconds")
            n_redirected += 1
    # Also drop any edges where source = node_id (rare for residuals, but safe)
    graph["edges"] = [e for e in graph["edges"] if e.get("source") != node_id]
    # Drop the node
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != node_id]
    return n_redirected


def recount_meta(graph):
    nodes = graph["nodes"]
    edges = graph["edges"]
    meta  = graph["meta"]
    meta["node_count"] = len(nodes)
    meta["edge_count"] = len(edges)
    meta["node_class_counts"] = {}
    meta["node_subtype_counts"] = {}
    for n in nodes:
        nc = n["node_class"]; ns = n["node_subtype"]
        meta["node_class_counts"][nc]   = meta["node_class_counts"].get(nc, 0) + 1
        meta["node_subtype_counts"][ns] = meta["node_subtype_counts"].get(ns, 0) + 1
    meta["edge_type_counts"] = {}
    for e in edges:
        et = e["edge_type"]
        meta["edge_type_counts"][et] = meta["edge_type_counts"].get(et, 0) + 1
    meta["starter_status_counts"] = {}
    for n in nodes:
        s = n.get("starter_status")
        if s:
            meta["starter_status_counts"][s] = meta["starter_status_counts"].get(s, 0) + 1


def main():
    raw = GRAPH.read_text(encoding="utf-8")
    BACKUP.write_text(raw, encoding="utf-8")
    print(f"backup -> {BACKUP.name}")
    graph = json.loads(raw)
    n_before = (graph["meta"]["node_count"], graph["meta"]["edge_count"])

    # Build (SITE, STATE) -> existing_node_id reverse lookup
    site_state_to_existing = {}
    for existing_id, sites in EXISTING_MATCHES.items():
        for site, state in sites:
            site_state_to_existing[(site, state)] = existing_id

    # 1. Drop 5 PADD residuals + redirect inflow edges
    print()
    print("=== 1. Drop PADD residuals and redirect inflow edges ===")
    total_redirected = 0
    for residual_id, redirect in PADD_RESIDUAL_REDIRECTS.items():
        n_red = drop_node_and_redirect_edges(graph, residual_id, redirect)
        print(f"  dropped {residual_id}; {n_red} inflow edge(s) redirected to {redirect}")
        total_redirected += n_red
    print(f"  total edges redirected: {total_redirected}")

    # 2. Add 10 refining-district aggregate nodes
    print()
    print("=== 2. Add refining-district aggregate nodes ===")
    for duoarea in sorted(SUB_PADD_DUOAREAS):
        node = make_district_node(duoarea)
        action = upsert_node(graph, node)
        print(f"  district {duoarea:>4} {action:>7}: {node['id']}")

    # 3. Process the capacity report
    print()
    print("=== 3. Process capacity report — match + upsert refineries ===")
    cap = load_capacity_report()
    print(f"  capacity report rows (TOTAL OPERABLE, calendar day): {len(cap)}")

    # Track which existing refineries get matched (so we can flag any that don't)
    existing_matched = set()

    n_matched_updates = 0
    n_new_added = 0
    n_skipped_constituent = 0

    for _, row in cap.iterrows():
        site_state = (row["SITE"], row["STATE_NAME"])
        existing_id = site_state_to_existing.get(site_state)

        if existing_id is not None:
            # This report row corresponds to a constituent of an existing node.
            # If the existing node is a single-site match, update it. Otherwise
            # (multi-constituent aggregate like marathon_la / salt_lake_cluster),
            # skip to avoid creating sub-nodes that double-count.
            constituent_count = len(EXISTING_MATCHES[existing_id])
            existing_matched.add(existing_id)
            if constituent_count > 1:
                n_skipped_constituent += 1
                continue

            duoarea = DISTRICT_INFO[row["RDIST_LABEL"]]["duoarea"]
            parent_view = parent_view_for_district(duoarea)
            node = make_refinery_node(row, existing_id, parent_view)
            # Preserve "kind" if set on the existing node
            for n in graph["nodes"]:
                if n["id"] == existing_id and "kind" in n:
                    node["kind"] = n["kind"]
                    break
            upsert_node(graph, node)
            n_matched_updates += 1
        else:
            duoarea = DISTRICT_INFO[row["RDIST_LABEL"]]["duoarea"]
            parent_view = parent_view_for_district(duoarea)
            new_id = make_new_refinery_id(row)
            # Disambiguate if the slug collides with another already-added refinery
            if any(n["id"] == new_id for n in graph["nodes"]):
                new_id = f"{new_id}_{slugify(row['STATE_NAME'])}"
            node = make_refinery_node(row, new_id, parent_view)
            upsert_node(graph, node)
            n_new_added += 1

    print(f"  matched-existing updated:     {n_matched_updates}")
    print(f"  new refineries added:         {n_new_added}")
    print(f"  skipped constituents (cluster aggregates): {n_skipped_constituent}")
    unmatched_existing = set(EXISTING_MATCHES) - existing_matched
    if unmatched_existing:
        print(f"  WARN: existing refineries NOT matched in report: {sorted(unmatched_existing)}")
    else:
        print(f"  all 19 existing named refineries matched in report.")

    # 4. Recount + write
    recount_meta(graph)
    n_after = (graph["meta"]["node_count"], graph["meta"]["edge_count"])
    print()
    print("=== 4. Final counts ===")
    print(f"  nodes: {n_before[0]} -> {n_after[0]}")
    print(f"  edges: {n_before[1]} -> {n_after[1]}")
    print(f"  node_subtype_counts:")
    for k, v in sorted(graph["meta"]["node_subtype_counts"].items()):
        print(f"    {k:<35} {v}")

    GRAPH.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"wrote {GRAPH.name}")


if __name__ == "__main__":
    main()
