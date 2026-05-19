"""One-shot patch for assign_eia.ipynb: Canadian asymmetry fix at PADDs 4 & 5.

Three edits to the notebook's source cells:

  1. EIA_SERIES list — add 2 new tuples mirroring the P1/P2/P3 pattern so
     MCRIPP4CA2 -> inflow__crude__padd4_view__canadian_oil_sands and
     MCRIPP5CA2 -> inflow__crude__padd5_view__canadian_oil_sands.
     (Currently bound only at pipe level: pipe_express_platte / pipe_trans_mountain_tmx.)

  2. FOREIGN_SUPPLY_P4 / FOREIGN_SUPPLY_P5 tuples — refresh the notes to
     reflect that foreign_supply at P4/P5 is now properly NON-Canadian
     (total minus Canadian), consistent with P1/P2/P3.

  3. FOREIGN_SUPPLY_REGIONS list — wire the Canadian-inflow series for P4/P5
     so the derived foreign_supply timeseries is built as
     MCRIPP{N}2 - MCRIPP{N}CA2 instead of MCRIPP{N}2 directly.

Idempotent: re-running detects already-applied edits and skips.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

NB = Path(__file__).parent / "assign_eia.ipynb"

# --- Edit 1: insert 2 new tuples after the MCRIPP3CA2 tuple ---
EDIT1_ANCHOR = (
    '    (\"MCRIPP3CA2\", \"eia:MCRIPP3CA2\", \"padd3_view\", \'canadian_oil_sands\', \"inflow\",\n'
    '        \"PADD 3 imports from Canada\", 1.0, \"kbd\", \"authoritative\",\n'
    '        \"inflow__crude__padd3_view__canadian_oil_sands\"),\n'
)
EDIT1_REPLACE = EDIT1_ANCHOR + (
    '    (\"MCRIPP4CA2\", \"eia:MCRIPP4CA2\", \"padd4_view\", \'canadian_oil_sands\', \"inflow\",\n'
    '        \"PADD 4 imports from Canada (Express+Platte corridor)\", 1.0, \"kbd\", \"authoritative\",\n'
    '        \"inflow__crude__padd4_view__canadian_oil_sands\"),\n'
    '    (\"MCRIPP5CA2\", \"eia:MCRIPP5CA2\", \"padd5_view\", \'canadian_oil_sands\', \"inflow\",\n'
    '        \"PADD 5 imports from Canada (TMX corridor)\", 1.0, \"kbd\", \"authoritative\",\n'
    '        \"inflow__crude__padd5_view__canadian_oil_sands\"),\n'
)

# --- Edit 2: refresh the FOREIGN_SUPPLY_P4 / P5 descriptions ---
EDIT2A_OLD = (
    '    # PADD 4: MCRIPP42 (no Canadian-only series; foreign_supply = total directly)\n'
    '    (\"MCRIPP42\", \"eia:MCRIPP42_foreign_supply\", \"padd4_view\", \"foreign_supply\", \"inflow\",\n'
    '        \"PADD 4 foreign crude imports (no Canadian breakdown; total = foreign_supply)\",\n'
)
EDIT2A_NEW = (
    '    # PADD 4: MCRIPP42 - MCRIPP4CA2\n'
    '    (\"FOREIGN_SUPPLY_P4\", \"eia:foreign_supply_to_padd4_kbd\", \"padd4_view\", \"foreign_supply\", \"inflow\",\n'
    '        \"PADD 4 non-Canadian foreign crude imports (derived)\",\n'
)
EDIT2B_OLD = (
    '    # PADD 5: MCRIPP52 (no Canadian-only series)\n'
    '    (\"MCRIPP52\", \"eia:MCRIPP52_foreign_supply\", \"padd5_view\", \"foreign_supply\", \"inflow\",\n'
    '        \"PADD 5 foreign crude imports (no Canadian breakdown; total = foreign_supply)\",\n'
)
EDIT2B_NEW = (
    '    # PADD 5: MCRIPP52 - MCRIPP5CA2\n'
    '    (\"FOREIGN_SUPPLY_P5\", \"eia:foreign_supply_to_padd5_kbd\", \"padd5_view\", \"foreign_supply\", \"inflow\",\n'
    '        \"PADD 5 non-Canadian foreign crude imports (derived)\",\n'
)

# --- Edit 3: FOREIGN_SUPPLY_REGIONS — wire Canadian inflow for P4 / P5 + rename ts_ids ---
EDIT3_OLD = (
    '    (\"P4\",    \"padd4_view\", \"inflow__crude__padd4_view__foreign_supply\", \"eia:MCRIPP42\", None),\n'
    '    (\"P5\",    \"padd5_view\", \"inflow__crude__padd5_view__foreign_supply\", \"eia:MCRIPP52\", None),\n'
)
EDIT3_NEW = (
    '    (\"P4\",    \"padd4_view\", \"inflow__crude__padd4_view__foreign_supply\", \"eia:MCRIPP42\", \"eia:MCRIPP4CA2\"),\n'
    '    (\"P5\",    \"padd5_view\", \"inflow__crude__padd5_view__foreign_supply\", \"eia:MCRIPP52\", \"eia:MCRIPP5CA2\"),\n'
)

# --- Edit 4: rename DERIVED_TS_ID entries for P4/P5 to the new ts_ids ---
EDIT4_OLD = (
    '    \"P4\":  \"eia:MCRIPP42_foreign_supply\",\n'
    '    \"P5\":  \"eia:MCRIPP52_foreign_supply\",\n'
)
EDIT4_NEW = (
    '    \"P4\":  \"eia:foreign_supply_to_padd4_kbd\",\n'
    '    \"P5\":  \"eia:foreign_supply_to_padd5_kbd\",\n'
)


def apply_one(cell_source: str, old: str, new: str, label: str) -> tuple[str, bool, bool]:
    """Returns (new_source, applied_now, already_done)."""
    if new in cell_source and old not in cell_source:
        return cell_source, False, True
    if old not in cell_source:
        return cell_source, False, False
    return cell_source.replace(old, new, 1), True, False


def main() -> None:
    nb = json.loads(NB.read_text(encoding="utf-8"))
    edits = [
        ("E1: add MCRIPP4/5CA2 -> padd4/5 canadian inflow tuples", EDIT1_ANCHOR, EDIT1_REPLACE),
        ("E2a: rename FOREIGN_SUPPLY_P4 tuple",                     EDIT2A_OLD,   EDIT2A_NEW),
        ("E2b: rename FOREIGN_SUPPLY_P5 tuple",                     EDIT2B_OLD,   EDIT2B_NEW),
        ("E3: wire Canadian inflow ts for P4/P5 in FOREIGN_SUPPLY_REGIONS", EDIT3_OLD, EDIT3_NEW),
        ("E4: rename DERIVED_TS_ID for P4/P5",                      EDIT4_OLD,    EDIT4_NEW),
    ]

    summary = []
    for cell in nb["cells"]:
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        new_source = source
        for label, old, new in edits:
            new_source, applied, already = apply_one(new_source, old, new, label)
            if applied:
                summary.append(f"  [applied]      {label}")
            elif already:
                summary.append(f"  [already-done] {label}")
        if new_source != source:
            cell["source"] = new_source.splitlines(keepends=True)

    missing = [lbl for (lbl, _o, _n) in edits if not any(lbl in s for s in summary)]
    if missing:
        print("ERROR: anchor text not found for these edits:")
        for m in missing:
            print(f"   - {m}")
        sys.exit(1)

    NB.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
    print("Edits applied:")
    for line in summary:
        print(line)
    print(f"Wrote {NB}")


if __name__ == "__main__":
    main()
