"""Second pass: clean up remaining stale references in v45.docx.

Loads v45.docx (from the first-pass script), applies the second-pass edits
(remaining 1,830/409 references, Table 22 cell updates with phi kept as a
designed type but value set to 0 to reflect the rollback of the grade trial,
Annex D.2 'difference' paragraph rewritten, Annex D.3 totals), and writes
back to v45.docx (overwrites). The original v44 is left untouched.

Context for phi (from Pedro): the variable-type 'phi' was populated in a
historical grade-decomposition trial that was rolled back to finalise the
single-commodity case study. Phi remains in the schema design but the count
is 0 in the active starter scenario.
"""
from __future__ import annotations
import sys, zipfile, re
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
DST = THIS_DIR.parent / "outputs" / "docs" / "Master_Thesis_Pedro_Porfirio_v45.docx"


# ---------------------------------------------------------------------------
# Bulk replacements (safe — substrings unique to numerical claims)
# ---------------------------------------------------------------------------

BULK_REPLACEMENTS = [
    # All remaining "1,830" occurrences -> 1,870
    # (the original first-pass script only changed two of them; the rest are
    # in Annex D, the summary block, and several mid-chapter sentences)
    # We use replace_all on the literal '1,830'. Verified by inspection that
    # the only other '1,830' candidate was inside Table 22 as a column value;
    # all such usages refer to the variable count and should become 1,870.
    ("1,830", "1,870"),

    # Remaining "409 directed flow edges" in summary block -> 433
    ("409 directed flow edges", "433 directed flow edges"),

    # "285,480 (variable, date) pairs" -> 291,564 (live count)
    ("285,480 (variable, date) pairs", "291,564 (variable, date) pairs"),
    ("Resolved (variable, date) pairs: 285,480", "Resolved (variable, date) pairs: 291,564"),

    # "80 authoritative bindings collectively produce all" -> "90 ..."
    ("The 80 authoritative bindings collectively produce all",
     "The 90 authoritative bindings collectively produce all"),

    # "80 time-series-observed variables" -> 90
    ("The 80 time-series-observed variables in the starter scenario",
     "The 90 time-series-observed variables in the starter scenario"),

    # "1,830 rows for June 2024 (one per variable)" — already covered by '1,830' bulk

    # Annex D.2 — "the seven variable types are listed in Table 21" framing is fine;
    # the prose still treats phi as a designed type. No edit there.

    # Annex D.2 'difference' paragraph rewrite. The full sentence is:
    #   "The 245-vs-240 difference for the non-relational types arises because three
    #   nodes have multiple commodity bindings (planned future-work decomposition into
    #   light sweet and heavy sour grades; not yet active in the resolver, but the
    #   variables exist as latent placeholders)."
    # In the post-rollback state, every non-relational type has exactly one row per
    # node, and phi is empty. Replace with prose that reflects that and keeps the
    # forward-looking grade note.
    (
        "The 245-vs-240 difference for the non-relational types arises because three nodes have multiple commodity bindings (planned future-work decomposition into light sweet and heavy sour grades; not yet active in the resolver, but the variables exist as latent placeholders).",
        "Each non-relational variable type carries exactly one row per node, since the starter scenario operates with a single commodity (crude). The phi count of zero reflects the rollback of an interim grade-decomposition trial that was carried out during development to validate the schema's multi-commodity handling; phi variables and per-grade decomposition will be reintroduced when grade-level work becomes the next workstream."
    ),
]


# ---------------------------------------------------------------------------
# Table 22 cell updates (anchored on row labels)
# ---------------------------------------------------------------------------

# Each entry: (row label inside <w:t>...</w:t>, OLD value with <w:t>X</w:t> wrapper, NEW value)
# We find the row label, then update the FIRST occurrence of the old-value run
# within a short window after the label (one row's worth of XML).
TABLE22_EDITS = [
    ("<w:t>Production</w:t>",       "<w:t>245</w:t>", "<w:t>251</w:t>"),
    ("<w:t>Consumption</w:t>",      "<w:t>245</w:t>", "<w:t>251</w:t>"),
    ("<w:t>Inventory</w:t>",        "<w:t>245</w:t>", "<w:t>251</w:t>"),
    ("<w:t>Balancing item</w:t>",   "<w:t>245</w:t>", "<w:t>251</w:t>"),
    ("<w:t>Inflow</w:t>",           "<w:t>410</w:t>", "<w:t>433</w:t>"),
    ("<w:t>Outflow</w:t>",          "<w:t>410</w:t>", "<w:t>433</w:t>"),
    ("<w:t>Phi (reference)</w:t>",  "<w:t>30</w:t>",  "<w:t>0</w:t>"),
    # Total row uses the literal "1,830" cell — already swept by BULK_REPLACEMENTS
]


def apply_table22_edits(xml: str) -> tuple[str, list[str]]:
    notes = []
    for anchor, old_t, new_t in TABLE22_EDITS:
        pos = xml.find(anchor)
        if pos == -1:
            notes.append(f"  Table 22: anchor {anchor!r} not found")
            continue
        # Search within 600 chars after the anchor (one row's worth of XML)
        window = xml[pos:pos + 600]
        rel = window.find(old_t)
        if rel == -1:
            notes.append(f"  Table 22: value {old_t!r} not in row for {anchor!r}")
            continue
        abs_pos = pos + rel
        xml = xml[:abs_pos] + new_t + xml[abs_pos + len(old_t):]
        notes.append(f"  Table 22: {anchor!r}  {old_t} -> {new_t}")
    return xml, notes


# ---------------------------------------------------------------------------
# Annex D.3 — "Total 80" -> "Total 90" in the authoritative-bindings table footer.
# The table has a 'Total' row whose value cell holds <w:t>80</w:t>.
# We anchor on '<w:t>Total</w:t>' and find the FOLLOWING '<w:t>80</w:t>' value.
# Caveat: there are multiple 'Total' rows in the document (one in Table 22, one
# in Annex D.3, possibly others). Table 22 'Total' has '<w:t>1,830</w:t>' (now
# '<w:t>1,870</w:t>' after bulk replace), so it won't match this. We confine
# the search to a single window.
# ---------------------------------------------------------------------------

def fix_authoritative_bindings_total(xml: str) -> tuple[str, list[str]]:
    notes = []
    # The right Total row sits between '<w:t>Total</w:t>' and the next paragraph
    # that begins 'The 90 authoritative bindings' (after our bulk replace). Find
    # that following paragraph, then walk BACKWARDS to the most recent
    # '<w:t>Total</w:t><...><w:t>80</w:t>' pair.
    landmark = "The 90 authoritative bindings"
    lm_pos = xml.find(landmark)
    if lm_pos == -1:
        notes.append("  Annex D.3: landmark 'The 90 authoritative bindings' not found")
        return xml, notes
    # Walk back 2500 chars (table is moderate in size) and find the LAST
    # '<w:t>Total</w:t>' before lm_pos.
    window_start = max(0, lm_pos - 2500)
    window = xml[window_start:lm_pos]
    last_total = window.rfind("<w:t>Total</w:t>")
    if last_total == -1:
        notes.append("  Annex D.3: 'Total' anchor not found before landmark")
        return xml, notes
    abs_total = window_start + last_total
    # Now find the next '<w:t>80</w:t>' after abs_total (within 1200 chars).
    sub = xml[abs_total:abs_total + 1200]
    rel = sub.find("<w:t>80</w:t>")
    if rel == -1:
        notes.append("  Annex D.3: '<w:t>80</w:t>' not found in Total row")
        return xml, notes
    abs_val = abs_total + rel
    xml = xml[:abs_val] + "<w:t>90</w:t>" + xml[abs_val + len("<w:t>80</w:t>"):]
    notes.append("  Annex D.3: Total 80 -> 90")
    return xml, notes


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main() -> int:
    if not DST.exists():
        print(f"ERROR: source not found: {DST}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(DST) as z:
        original_xml = z.read("word/document.xml").decode("utf-8")
        other_files = {name: z.read(name) for name in z.namelist() if name != "word/document.xml"}

    xml = original_xml
    notes: list[str] = []

    # Bulk replacements
    for old, new in BULK_REPLACEMENTS:
        if old == new:
            continue
        count = xml.count(old)
        if count == 0:
            notes.append(f"  BULK: NOT FOUND: {old[:80]!r}")
            continue
        xml = xml.replace(old, new)
        notes.append(f"  BULK: {count}x  {old[:60]!r} -> {new[:60]!r}")

    # Table 22 cell updates
    xml, n = apply_table22_edits(xml)
    notes.extend(n)

    # Annex D.3 Total fix
    xml, n = fix_authoritative_bindings_total(xml)
    notes.extend(n)

    # Sanity: still balanced
    open_tbl = xml.count("<w:tbl>")
    close_tbl = xml.count("</w:tbl>")
    open_tr = xml.count("<w:tr ") + xml.count("<w:tr>")
    close_tr = xml.count("</w:tr>")
    sanity = open_tbl == close_tbl and open_tr == close_tr
    print(f"Sanity: <w:tbl> {open_tbl}/{close_tbl}, <w:tr> {open_tr}/{close_tr}  -- {'OK' if sanity else 'BROKEN'}")

    # Write back (overwrite v45)
    with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in other_files.items():
            zout.writestr(name, data)
        zout.writestr("word/document.xml", xml.encode("utf-8"))
    print(f"Updated: {DST.name}  ({DST.stat().st_size:,} bytes)\n")
    print("Edits applied:")
    for n in notes:
        print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
