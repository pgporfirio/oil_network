"""One-shot: apply consistency edits to v44 -> v45.docx (+ matching PDF).

Reads outputs/docs/Master_Thesis_Pedro_Porfirio_v44.docx, applies a targeted
set of text and table-row edits to align numerical claims and view inventories
with the live stage_1_complete DB state (run_id=14), then writes v45.docx and
v45.pdf. Throwaway script — checked in for one rebuild then deletable.

Edits are organised in three groups:
  A. Plain string replacements anchored by enough surrounding context to be
     unambiguous (headline numbers, prose dispatch counts, file-name swaps in
     section 5.9, DDL block, 'five categories' -> 'six categories').
  B. Table 8 (source enum): insert a 'partial' row before </w:tbl>.
  C. Table 9 (dispatch counts): merge 'alias'+'arithmetic' rows into a single
     'arithmetic' row with value 451; update other row values.
  D. Table 10 (layered views): insert L3c v_partition_sums and L3d
     v_partition_intra_flows rows after L3b v_flow_edges.

Anything that would require multi-paragraph prose rewriting (the alias-vs-
arithmetic rule narrative, the 11 missing migration entries in Table 26) is
left untouched and reported at the end as remaining work.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
DOCS_DIR = THIS_DIR.parent / "outputs" / "docs"
SRC = DOCS_DIR / "Master_Thesis_Pedro_Porfirio_v44.docx"
DST = DOCS_DIR / "Master_Thesis_Pedro_Porfirio_v45.docx"


# ---------------------------------------------------------------------------
# A. Plain string replacements (each must be a unique substring of the XML)
# ---------------------------------------------------------------------------

REPLACEMENTS = [
    # --- §5.2 headline ¶251 (Abstract / case-study intro) -----------------
    ("240 assets, 409 directed flow edges, and 1,830 variables, of which 80 are time-series-observed",
     "251 assets, 433 directed flow edges, and 1,870 variables, of which 90 are time-series-observed"),

    # --- §5.2 ¶252 -------------------------------------------------------
    ("It contains 240 assets in total: 197 physical and 43 abstract aggregates, of which 4 are boundary nodes.",
     "It contains 251 assets in total: 217 physical and 34 abstract aggregates, of which 3 are boundary nodes."),
    ("The assets connect via 409 directed flow edges, and they carry 1,830 variables under the active scenario",
     "The assets connect via 433 directed flow edges, and they carry 1,870 variables under the active scenario"),

    # --- §5.7 ¶282 partition tree --------------------------------------
    ("The starter scenario carries 265 partition edges across 28 distinct parents and 197 distinct children.",
     "The starter scenario carries 377 partition edges across 29 distinct parents and 211 distinct children."),

    # --- §5.9 Phase 1 file names ---------------------------------------
    # assign_eia.ipynb writes timeseries/timeseries_data -> should be load_eia.ipynb
    ("The EIA series catalogue and monthly facts are loaded (",
     "The EIA series catalogue and monthly facts are loaded ("),  # no-op anchor for surrounding context

    # The actual swap (assign_eia.ipynb -> load_eia.ipynb in the Phase 1 paragraph).
    # The XML splits 'assign_eia' across runs in some places, but the run holding
    # the bare 'assign_eia.ipynb' is in §5.9 only - replace just the one paragraph
    # by searching for its unique surrounding text.
    # Anchored by 'writes to', 'timeseries', 'timeseries_data' triplet.
    # The 'assign_eia.ipynb' string is in a Consolas-styled run (file name) - we
    # replace the bare text content.
    # Multiple Consolas-styled runs contain 'assign_eia.ipynb' so we replace by
    # surrounding context.

    # --- Migration family list in §5.9 ---------------------------------
    # 'apply_*.py' doesn't exist; replace with 'patch_*.py'
    (">apply_*.py<", ">patch_*.py<"),

    # --- §6.2 ¶329 resolver runtime sentence ---------------------------
    ("(1,830 variables × 156 monthly observations)",
     "(1,870 variables × 156 monthly observations)"),

    # --- §6.2 ¶338 DDL block: add 'partial' to the source CHECK --------
    ("('observed', 'derived', 'zero', 'latent', 'unresolved')),",
     "('observed', 'derived', 'zero', 'latent', 'unresolved', 'partial')),"),

    # --- §6.2 ¶347 'five categories' -----------------------------------
    ("The </w:t></w:r><w:r><w:rPr><w:rFonts w:ascii=\"Consolas\" w:eastAsia=\"Consolas\" w:hAnsi=\"Consolas\" w:cs=\"Consolas\"/></w:rPr><w:t>source</w:t></w:r><w:r><w:t xml:space=\"preserve\"> column is a constrained enum with five categories",
     "The </w:t></w:r><w:r><w:rPr><w:rFonts w:ascii=\"Consolas\" w:eastAsia=\"Consolas\" w:hAnsi=\"Consolas\" w:cs=\"Consolas\"/></w:rPr><w:t>source</w:t></w:r><w:r><w:t xml:space=\"preserve\"> column is a constrained enum with six categories"),

    # --- §6.4 Rule 1 (observed) prose count ----------------------------
    # The thesis Rule 1 prose doesn't give a count; the count appears in
    # Table 9 only.

    # --- §6.4 Rule 2 (zero) prose count --------------------------------
    ("Roughly 628 variables fire this rule",
     "Roughly 542 variables fire this rule"),

    # --- §6.4 Rule 3 (latent) prose count ------------------------------
    ("The rule fires 652 times in the starter scenario after the promotion attempt",
     "The rule fires 767 times in the starter scenario after the promotion attempt"),

    # --- §6.4 Rule 5 (alias) prose count -------------------------------
    # Code subsumed alias into arithmetic, so the rule is now described as
    # the single-term arithmetic case. Cheapest edit: update the count and
    # acknowledge the rename in a parenthetical.
    ("in the starter scenario, 442 variables resolve via alias.",
     "in the starter scenario, the arithmetic rule resolves 451 variables in total, the majority via this single-variable case (the code labels this dispatch kind 'arithmetic'; earlier drafts called it 'alias')."),

    # --- §6.5 (recursive resolver equivalence) -------------------------
    # The equivalence sentence already uses the right numbers (291,564; obs 90;
    # zero 542; latent 767; sum 5; arithmetic 451; reverse_mirror 15) -- confirmed
    # by inspection at @404034. No edit needed.

    # --- §6.7 ¶421 latent count ----------------------------------------
    ("The starter scenario at the time of writing has 652 latents and zero unresolved. The 652 latents",
     "The starter scenario at the time of writing has 767 latents and zero unresolved. The 767 latents"),

    # --- §7.1 ¶450 single-attribution count ---------------------------
    ("80 distinct time series are bound across 80 assignment rows",
     "90 distinct time series are bound across 90 assignment rows"),

    # --- §5.7 ¶305 / §6.2 / §7.2 'twelve passes' ---------------------
    ("After twelve passes of refinement, this closure holds within rounding tolerance",
     "After twenty-three passes of refinement, this closure holds within rounding tolerance"),
    ("in both the inflow and outflow directions, after twelve passes of refinement to the assignment set",
     "in both the inflow and outflow directions, after twenty-three passes of refinement to the assignment set"),
    ("The current state of the starter scenario is the result of running all twelve passes in sequence.",
     "The current state of the starter scenario is the result of running all twenty-three passes in sequence."),
    ("The starter graph reached its current state through twelve migration passes",
     "The starter graph reached its current state through twenty-three migration passes"),

    # --- §8 end-of-doc summary block "Nodes: 240 (197 physical, 43 abstract; including 4 boundary)" ---
    ("240 (197 physical, 43 abstract; including 4 boundary)",
     "251 (217 physical, 34 abstract; including 3 boundary)"),
]


# ---------------------------------------------------------------------------
# B. Table 8: add 'partial' row before </w:tbl>
# ---------------------------------------------------------------------------

# The 'partial' row, modeled on the 'unresolved' row at the end of Table 8.
PARTIAL_ROW = (
    '<w:tr w:rsidR="00537923" w14:paraId="1E2030A0" w14:textId="77777777">'
    '<w:tc><w:tcPr><w:tcW w:w="4533" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>'
    '<w:p w14:paraId="3B168066" w14:textId="77777777" w:rsidR="00537923" w:rsidRDefault="005C5514">'
    '<w:r><w:t>partial</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:tcPr><w:tcW w:w="4534" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>'
    '<w:p w14:paraId="694884ED" w14:textId="77777777" w:rsidR="00537923" w:rsidRDefault="005C5514">'
    '<w:r><w:t>Formula tried but at least one input was NULL on that date; value is NULL with a recorded note</w:t></w:r></w:p></w:tc>'
    '</w:tr>'
)

# Anchor: the 'unresolved' row that ends Table 8 closes with this exact
# sequence (verified by inspection). The PARTIAL_ROW is inserted right after.
TABLE8_UNRESOLVED_ROW_END_ANCHOR = (
    '<w:t>A formula was given but could not be evaluated. Zero rows in a healthy run</w:t>'
    '</w:r></w:p></w:tc></w:tr></w:tbl>'
)
TABLE8_REPLACEMENT = (
    '<w:t>A formula was given but could not be evaluated. Zero rows in a healthy run</w:t>'
    '</w:r></w:p></w:tc></w:tr>' + PARTIAL_ROW + '</w:tbl>'
)


# ---------------------------------------------------------------------------
# C. Table 9: merge alias + arithmetic, update counts
# ---------------------------------------------------------------------------

# Strategy: do the value edits as plain string replacements anchored by
# the row labels in the run preceding the value. Table 9 stores each cell
# in a separate <w:tc>...<w:t>VALUE</w:t>... block, so we have to swap by
# anchoring on the row label text.

# Inspecting @386500: Rule|Count|observed (time-series lookup)|80|zero (structural)|628|...
# Each pair is "label-run, value-run". The value '80' for 'observed' is the
# first <w:t>80</w:t> after the 'observed (time-series lookup)' text.

TABLE9_VALUE_EDITS = [
    # (anchor: label text run that uniquely identifies the row, old_value_t, new_value_t)
    ("observed (time-series lookup)", "<w:t>80</w:t>", "<w:t>90</w:t>"),
    ("zero (structural)",             "<w:t>628</w:t>", "<w:t>542</w:t>"),
    ("latent (including reverse-mirror failures)", "<w:t>652</w:t>", "<w:t>767</w:t>"),
    # The 'alias' row becomes the merged 'arithmetic' row.
    # Label change: "alias (single-variable)" -> "arithmetic (incl. single-variable)"
    # Value: 442 -> 451
    # (handled separately below via two surgical edits)
    ("reverse-mirror (Rule 3 promotion plus Rule 6)", "<w:t>15</w:t>", "<w:t>15</w:t>"),  # no-op anchor
]


def apply_table9_value_edits(xml: str) -> tuple[str, list[str]]:
    """Apply per-row Table 9 value updates by finding the FIRST occurrence of
    `old_value_t` AFTER the row-label anchor. Returns (xml, notes)."""
    notes = []
    for anchor, old_t, new_t in TABLE9_VALUE_EDITS:
        if old_t == new_t:
            continue
        anchor_pos = xml.find(anchor)
        if anchor_pos == -1:
            notes.append(f"  Table 9: anchor not found: {anchor!r}")
            continue
        # Restrict search to within ~600 chars after the anchor (one row).
        search_window = xml[anchor_pos:anchor_pos + 600]
        if old_t not in search_window:
            notes.append(f"  Table 9: value {old_t!r} not in row for {anchor!r}")
            continue
        rel = search_window.find(old_t)
        abs_pos = anchor_pos + rel
        xml = xml[:abs_pos] + new_t + xml[abs_pos + len(old_t):]
        notes.append(f"  Table 9: {anchor!r}  {old_t} -> {new_t}")
    return xml, notes


def merge_alias_arithmetic_rows(xml: str) -> tuple[str, list[str]]:
    """Two surgical edits:
      (i)  rename "alias (single-variable)" row label and update its value 442 -> 451
      (ii) delete the "arithmetic" row entirely (the one with value 8)
    Both done by anchoring on the row labels.
    """
    notes = []

    # (i) rename "alias (single-variable)" -> "arithmetic" and 442 -> 451
    old_label = '<w:t>alias (single-variable)</w:t>'
    new_label = '<w:t>arithmetic</w:t>'
    if old_label in xml:
        xml = xml.replace(old_label, new_label, 1)
        notes.append("  Table 9: row label 'alias (single-variable)' -> 'arithmetic'")
    else:
        notes.append("  Table 9: 'alias (single-variable)' label not found - skipping")

    # Update 442 -> 451 in the row immediately following 'arithmetic' (which
    # is the relabeled row).
    anchor = '<w:t>arithmetic</w:t>'
    pos = xml.find(anchor)
    if pos != -1:
        win = xml[pos:pos + 600]
        if '<w:t>442</w:t>' in win:
            rel = win.find('<w:t>442</w:t>')
            abs_pos = pos + rel
            xml = xml[:abs_pos] + '<w:t>451</w:t>' + xml[abs_pos + len('<w:t>442</w:t>'):]
            notes.append("  Table 9: arithmetic value 442 -> 451")
        else:
            notes.append("  Table 9: 442 not found after arithmetic label - skipping value swap")

    # (ii) delete the OLD 'arithmetic' row (value 8). After (i), the new
    # 'arithmetic' row holds 451. The OLD 'arithmetic' row had label
    # '<w:t>arithmetic</w:t>' and value '<w:t>8</w:t>'. To distinguish it
    # from the relabeled (i) row, we look for the SECOND '<w:t>arithmetic</w:t>'
    # in the table area.
    second_pos = xml.find(anchor, pos + 1)
    if second_pos == -1:
        notes.append("  Table 9: no second 'arithmetic' row to delete (already merged?)")
    else:
        # Find the enclosing <w:tr ...> ... </w:tr> for second_pos.
        tr_start = xml.rfind('<w:tr ', 0, second_pos)
        tr_end_marker = '</w:tr>'
        tr_end = xml.find(tr_end_marker, second_pos) + len(tr_end_marker)
        if tr_start == -1 or tr_end == -1:
            notes.append("  Table 9: could not locate enclosing <w:tr> for second 'arithmetic' - skipping delete")
        else:
            xml = xml[:tr_start] + xml[tr_end:]
            notes.append("  Table 9: deleted old 'arithmetic' row (value 8)")
    return xml, notes


# ---------------------------------------------------------------------------
# D. Table 10: insert L3c v_partition_sums and L3d v_partition_intra_flows
# ---------------------------------------------------------------------------

# Cell template (4 cells per row: tier, view, source, description). Built by
# mimicking the structure of the L3b row identified at @417259.

def build_table10_row(para_id: str, tier: str, view: str, source: str, desc: str) -> str:
    """Generate a single <w:tr> with 4 cells. Width values mimic the L3b row."""
    # Widths: 4 cells of equal-ish width totalling around 9067 dxa (matches L3a/L3b).
    widths = ["1200", "2200", "2200", "3467"]
    parts = ['<w:tr w:rsidR="00537923" w14:paraId="' + para_id + '" w14:textId="77777777">']
    for i, (w, t) in enumerate(zip(widths, [tier, view, source, desc])):
        parts.append('<w:tc>')
        parts.append('<w:tcPr><w:tcW w:w="' + w + '" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>')
        parts.append(
            '<w:p w14:paraId="' + para_id[:4] + ('CELL' + str(i))[:4] + '" '
            'w14:textId="77777777" w:rsidR="00537923" w:rsidRDefault="005C5514">'
            '<w:r><w:t xml:space="preserve">' + t + '</w:t></w:r></w:p>'
        )
        parts.append('</w:tc>')
    parts.append('</w:tr>')
    return ''.join(parts)


def insert_table10_rows(xml: str) -> tuple[str, list[str]]:
    """Insert L3c and L3d rows immediately after the L3b v_flow_edges row."""
    notes = []
    # L3b row's value cell contains 'v_flow_edges' followed by 'variables' in the
    # source cell. Find the '</w:tr>' closing that row.
    anchor = '<w:t>v_flow_edges</w:t>'
    pos = xml.find(anchor)
    if pos == -1:
        notes.append("  Table 10: 'v_flow_edges' anchor not found - skipping insertion")
        return xml, notes
    # Find the next </w:tr> after this point.
    end_marker = '</w:tr>'
    tr_end = xml.find(end_marker, pos)
    if tr_end == -1:
        notes.append("  Table 10: could not find </w:tr> after v_flow_edges - skipping")
        return xml, notes
    insert_at = tr_end + len(end_marker)

    l3c = build_table10_row(
        para_id="2A3F0001",
        tier="L3c",
        view="v_partition_sums",
        source="L2c + scenario_resolved_values",
        desc="Materialised observed-vs-children sum per (parent, variable_type, commodity, date)",
    )
    l3d = build_table10_row(
        para_id="2A3F0002",
        tier="L3d",
        view="v_partition_intra_flows",
        source="L2b + scenario_resolved_values",
        desc="Materialised internal flow accounting for partition closure across the partition tree",
    )

    xml = xml[:insert_at] + l3c + l3d + xml[insert_at:]
    notes.append("  Table 10: inserted L3c v_partition_sums + L3d v_partition_intra_flows rows after L3b")
    return xml, notes


# ---------------------------------------------------------------------------
# Special: §5.9 assign_eia -> load_eia in the Phase 1 paragraph
# ---------------------------------------------------------------------------

def fix_section59_filenames(xml: str) -> tuple[str, list[str]]:
    """The Phase 1 paragraph contains 'assign_eia.ipynb' as the writer of
    timeseries/timeseries_data, which is wrong (load_eia.ipynb does that).
    There may be multiple occurrences of 'assign_eia.ipynb' across the doc
    (e.g. NOTEBOOKS-style mentions), so we anchor on the surrounding context.
    """
    notes = []
    # The exact phrase from §5.9 inspection: 'are loaded (assign_eia.ipynb writes to timeseries'.
    # In XML the file name is in a Consolas run, surrounded by tag noise; replace
    # by anchoring on the bare text 'assign_eia.ipynb' that appears in the
    # 'monthly facts are loaded' context.
    # Look for the unique left-context 'monthly facts are loaded (' and the
    # bare 'assign_eia.ipynb' that follows.
    anchor_left = "monthly facts are loaded ("
    pos = xml.find(anchor_left)
    if pos == -1:
        notes.append("  §5.9: anchor 'monthly facts are loaded (' not found - file-name swap skipped")
        return xml, notes
    # Find the first 'assign_eia.ipynb' AFTER pos.
    search_from = pos
    target = "assign_eia.ipynb"
    rel = xml.find(target, search_from, search_from + 1500)
    if rel == -1:
        notes.append("  §5.9: 'assign_eia.ipynb' not found near anchor - swap skipped")
        return xml, notes
    xml = xml[:rel] + "load_eia.ipynb" + xml[rel + len(target):]
    notes.append("  §5.9: assign_eia.ipynb -> load_eia.ipynb (Phase 1 'monthly facts are loaded' sentence)")
    return xml, notes


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main() -> int:
    if not SRC.exists():
        print(f"ERROR: source not found: {SRC}", file=sys.stderr)
        return 1

    # Read v44 XML
    with zipfile.ZipFile(SRC) as z:
        original_xml = z.read("word/document.xml").decode("utf-8")
        other_files = {name: z.read(name) for name in z.namelist() if name != "word/document.xml"}

    xml = original_xml
    notes: list[str] = []

    # A. Plain replacements
    for old, new in REPLACEMENTS:
        if old == new:
            continue
        if old in xml:
            xml = xml.replace(old, new, 1)
            short_old = (old[:80] + "...") if len(old) > 80 else old
            short_new = (new[:80] + "...") if len(new) > 80 else new
            notes.append(f"  A: {short_old!r} -> {short_new!r}")
        else:
            short_old = (old[:80] + "...") if len(old) > 80 else old
            notes.append(f"  A: NOT FOUND: {short_old!r}")

    # B. Table 8 - insert 'partial' row
    if TABLE8_UNRESOLVED_ROW_END_ANCHOR in xml:
        xml = xml.replace(TABLE8_UNRESOLVED_ROW_END_ANCHOR, TABLE8_REPLACEMENT, 1)
        notes.append("  B: Table 8 - inserted 'partial' row before </w:tbl>")
    else:
        notes.append("  B: Table 8 anchor not found - 'partial' row not inserted")

    # C. Table 9 - per-row value edits + alias/arithmetic merge
    xml, n = apply_table9_value_edits(xml)
    notes.extend(n)
    xml, n = merge_alias_arithmetic_rows(xml)
    notes.extend(n)

    # D. Table 10 - insert L3c + L3d
    xml, n = insert_table10_rows(xml)
    notes.extend(n)

    # Special: §5.9 file name swap
    xml, n = fix_section59_filenames(xml)
    notes.extend(n)

    # Cheap validity check: XML must still be balanced for opening/closing
    # tags at a high level (just check counts of <w:tbl> match </w:tbl>).
    open_tbl = xml.count("<w:tbl>")
    close_tbl = xml.count("</w:tbl>")
    open_tr = xml.count("<w:tr ") + xml.count("<w:tr>")
    close_tr = xml.count("</w:tr>")
    sanity = open_tbl == close_tbl and open_tr == close_tr
    print(f"\nSanity: <w:tbl> {open_tbl}/{close_tbl}, <w:tr> {open_tr}/{close_tr}  -- {'OK' if sanity else 'BROKEN'}")

    # Write v45.docx
    with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in other_files.items():
            zout.writestr(name, data)
        zout.writestr("word/document.xml", xml.encode("utf-8"))

    print(f"\nWrote: {DST.name}  ({DST.stat().st_size:,} bytes vs source {SRC.stat().st_size:,})")
    print(f"Edits applied:")
    for note in notes:
        print(note)

    print()
    print("REMAINING WORK (not applied; needs prose rewriting or many table inserts):")
    print("  - Table 26 lists 12 migrations; full inventory is 23. Need 11 more rows.")
    print("  - §6.6 has multi-paragraph narrative around alias vs arithmetic - left unedited;")
    print("    only the headline count in §6.4 Rule 5 was updated.")
    print("  - Anywhere the prose mentions '5 dispatch kinds' or 'six rules' should be")
    print("    audited against the current dispatcher inventory (observed/zero/latent/sum/arithmetic).")
    print("  - §5.4 Table 22 lists 245 per variable type; verify against live counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
