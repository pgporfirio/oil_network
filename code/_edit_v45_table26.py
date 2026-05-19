"""Third pass: extend Table 26 (migration passes) from 12 -> 23 rows.

Inserts 11 new rows after row 12 ('Collapse three sum labels ...'). Each new
row mirrors row 12's XML structure. Pass numbers are 13..23, descriptions cite
the actual migration scripts in code/migrations/.

Multi-migration entries group thematically related scripts (e.g. row 22 covers
all three pipeline/capacity-timeline patches). The grouping was chosen to keep
the table narrative-readable rather than file-by-file; the full file listing
lives in code/migrations/.
"""
from __future__ import annotations
import sys, zipfile, re
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
DST = THIS_DIR.parent / "outputs" / "docs" / "Master_Thesis_Pedro_Porfirio_v45.docx"


def make_row(para_id: str, pass_no: str, description: str, effect: str) -> str:
    """Build a 3-cell row mirroring Table 26 row 12's structure."""
    return (
        f'<w:tr w:rsidR="00537923" w14:paraId="{para_id}" w14:textId="77777777">'
        f'<w:tc><w:tcPr><w:tcW w:w="3022" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>'
        f'<w:p w14:paraId="{para_id[:4]}AA01" w14:textId="77777777" w:rsidR="00537923" w:rsidRDefault="005C5514">'
        f'<w:r><w:t>{pass_no}</w:t></w:r></w:p></w:tc>'
        f'<w:tc><w:tcPr><w:tcW w:w="3022" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>'
        f'<w:p w14:paraId="{para_id[:4]}AA02" w14:textId="77777777" w:rsidR="00537923" w:rsidRDefault="005C5514">'
        f'<w:r><w:t xml:space="preserve">{description}</w:t></w:r></w:p></w:tc>'
        f'<w:tc><w:tcPr><w:tcW w:w="3023" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>'
        f'<w:p w14:paraId="{para_id[:4]}AA03" w14:textId="77777777" w:rsidR="00537923" w:rsidRDefault="005C5514">'
        f'<w:r><w:t xml:space="preserve">{effect}</w:t></w:r></w:p></w:tc>'
        f'</w:tr>'
    )


NEW_ROWS = [
    (
        "13",
        "thirteenth_pass_views.py: create the layered materialised views (v_flow_edges, v_aggregation_edges, v_partition_tree, v_node_status, v_node_balance_check, v_aggregation_consistency, v_inventory_changes, v_aggregate_balance, v_node_pcisob, v_formula_input_links, v_partition_sums, v_partition_intra_flows)",
        "Structural and analytic views become queryable; renderers and audits read these instead of joining variable_assignments directly",
    ),
    (
        "14",
        "sixteenth_pass_cleanup.py: JSONB attribute cleanup and introduction of v_effective_assignments override semantics",
        "Per-scenario overrides representable without rewriting base assignment rows",
    ),
    (
        "15",
        "seventeenth_pass_xstate_membership.py: declare pipe_bakken_xstate as an inventory constituent of its natural parents",
        "Bakken cross-state connector enters the partition tree; closure tightens at PADD-2 inflow",
    ),
    (
        "16",
        "eighteenth_pass_constraint_membership.py: declare seven constraint nodes as constituents of their natural parents",
        "Boundary-functional constraint nodes join the partition spine",
    ),
    (
        "17",
        "repoint_foreign_supply_to_imports_agg.py and repoint_canadian_corridor_ts.py: move foreign-supply TS authority to the physical aggregate and Canadian corridor TS to the pipeline nodes themselves",
        "Authoritative anchors move from ambiguous aggregate-level to specific physical nodes",
    ),
    (
        "18",
        "add_padd2_canadian_imports_agg.py and add_partition_aggregates.py: add the PADD-2 Canadian-imports prototype, then generalise to all PADD aggregates (19 abstract nodes added)",
        "Inter-PADD movement becomes natively addressable in the partition tree",
    ),
    (
        "19",
        "split_bakken_gathering.py and wire_bakken_xstate_into_inter_padds.py: split Bakken gathering into ND/MT components and wire the cross-state connector into the inter-PADD aggregates",
        "Bakken cross-state flow becomes explicit; closure improves on PADD 2/4 movement",
    ),
    (
        "20",
        "twenty_first_pass_spr_under_padd3.py and promote_spr_total_to_balance_role.py: place SPR sites under PADD-3 inventory and promote the SPR total from constraint to balance role",
        "USA-level double-counting of SPR resolved; SPR aggregates correctly via PADD 3",
    ),
    (
        "21",
        "add_padd_stock_decomposition.py: per-PADD decomposition of stocks into tank-farms-and-pipelines and refinery-stocks components",
        "Stocks decompose into authoritative sub-components; path opens to per-facility data when sourced",
    ),
    (
        "22",
        "refactor_jones_act_into_inter_padd_agg.py, patch_pipeline_production_capacities.py and patch_pipeline_timeline.py: move Jones-Act in-transit onto the inter-PADD aggregate; backfill pipeline and producer capacity for the present and across 2015-2025",
        "Capacity layer populated; capacity violations at 2024-12-01 drop to 0",
    ),
    (
        "23",
        "add_node_roles.py, add_aggregation_constituents.py, patch_v_aggregation_consistency_missing_data.py, patch_production_caps_from_peaks.py, add_canadian_pipelines_inventory_membership.py and build_us_crude_grade_map.py: role tagging (balance/constraint/auxiliary), aggregation-constituent cross-checks, partial-coverage classification for missing data, production-cap headroom from historical peaks, Canadian-pipeline inventory membership, and the crude-grade hierarchy seed (currently dormant after the grade-trial rollback)",
        "Consistency layer finalised; grade-registry placeholder remains for future work",
    ),
]


def main() -> int:
    if not DST.exists():
        print(f"ERROR: source not found: {DST}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(DST) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        other_files = {name: z.read(name) for name in z.namelist() if name != "word/document.xml"}

    # Find the row 12 closing </w:tr>, then </w:tbl> that immediately follows.
    anchor = "Collapse three sum labels"
    pos = xml.find(anchor)
    if pos == -1:
        print("ERROR: 'Collapse three sum labels' anchor not found", file=sys.stderr)
        return 2
    tr_end = xml.find("</w:tr>", pos) + len("</w:tr>")
    if not xml[tr_end:tr_end + len("</w:tbl>")] == "</w:tbl>":
        print("ERROR: expected </w:tbl> immediately after row 12; found:", file=sys.stderr)
        print(repr(xml[tr_end:tr_end + 200]), file=sys.stderr)
        return 3

    # Build the new rows
    blocks = []
    for i, (pass_no, desc, effect) in enumerate(NEW_ROWS):
        # Unique paraId per row (8 hex chars below 0x7FFFFFFF). Use a deterministic seed.
        para_id = f"3A2C{0x10 + i:04X}"
        blocks.append(make_row(para_id, pass_no, desc, effect))

    new_xml = xml[:tr_end] + "".join(blocks) + xml[tr_end:]

    # Sanity check
    o_tbl = new_xml.count("<w:tbl>")
    c_tbl = new_xml.count("</w:tbl>")
    o_tr = new_xml.count("<w:tr ") + new_xml.count("<w:tr>")
    c_tr = new_xml.count("</w:tr>")
    sane = o_tbl == c_tbl and o_tr == c_tr
    print(f"Sanity: tbl {o_tbl}/{c_tbl}, tr {o_tr}/{c_tr}  -- {'OK' if sane else 'BROKEN'}")

    with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in other_files.items():
            zout.writestr(name, data)
        zout.writestr("word/document.xml", new_xml.encode("utf-8"))
    print(f"Updated: {DST.name}  ({DST.stat().st_size:,} bytes)")
    print(f"Inserted {len(NEW_ROWS)} rows (passes 13..23) into Table 26.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
