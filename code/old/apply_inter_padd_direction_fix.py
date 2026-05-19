"""Patch assign_eia.ipynb to fix the inter-PADD direction-naming bug.

EIA convention: `MCRMP_P{A}_P{B}_1` means "PADD A receives by pipeline from
PADD B" — flow direction is B -> A. assign_eia.ipynb's COMBINED_INTER_PADD
list was reading the series name as if A were the sender, binding each
direction to the variable for the opposite direction. The raw-binding tuples
(lines 282-308, 404-418, 426-428) had the same inversion.

This patch:
  1. Swaps the (node, related_node) and rewrites the description for every
     raw inter-PADD series binding (MCRMP*, MCRMT*, MCRMP_R*) so the
     auxiliary catalogue rows reflect actual EIA direction.
  2. Rewrites the COMBINED_INTER_PADD tuples to use the opposite-direction
     raw series for each corridor's component list, so each
     `combined_inter_padd_P{A}_to_P{B}_kbd` derived series actually carries
     the P{A} -> P{B} value.

Re-run order after applying:
    python apply_inter_padd_direction_fix.py
    python -m jupyter nbconvert --to notebook --execute --inplace assign_eia.ipynb
    python -m jupyter nbconvert --to notebook --execute --inplace assign_formulas.ipynb
    python add_aggregation_constituents.py
    python add_node_roles.py
    python add_inter_padd_pipe_constituents.py
    python resolve_scenario.py --notes "Inter-PADD direction fix applied"
    python make_balance_resolver_ui.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

NB = Path(__file__).parent / "assign_eia.ipynb"


# -- 1. Raw inter-PADD bindings: swap (node, related_node) and description -----
# Format: old line -> new line. The line text matches the .ipynb JSON source
# (escaped quotes + trailing comma + \n).
#
# EIA series `MCRMP_P{A}_P{B}_1` has flow direction P{B} -> P{A}, so:
#   - node            should be paddB_view  (sender, outflow source)
#   - related_node    should be paddA_view  (receiver)
#   - description     should say "PADD B -> PADD A"
RAW_PATCHES = []
# MCRMP* (pipeline) raw bindings: 2-line format with "PADD A -> PADD B" description
for series_in, A, B in [
    ("MCRMPP1P21", 1, 2), ("MCRMPP2P11", 2, 1),
    ("MCRMPP2P31", 2, 3), ("MCRMPP2P41", 2, 4),
    ("MCRMPP3P11", 3, 1), ("MCRMPP3P21", 3, 2), ("MCRMPP3P41", 3, 4),
    ("MCRMPP4P21", 4, 2),
]:
    old = (f'    "    (\\"{series_in}\\", \\"eia:{series_in}\\", '
           f'\\"padd{A}_view\\", \\"padd{B}_view\\", \\"outflow\\",\\n",\n'
           f'    "        \\"Inter-PADD movement: PADD {A} -> PADD {B}\\", '
           f'1.0, \\"mbbl\\", \\"auxiliary\\",\\n",\n')
    new = (f'    "    (\\"{series_in}\\", \\"eia:{series_in}\\", '
           f'\\"padd{B}_view\\", \\"padd{A}_view\\", \\"outflow\\",\\n",\n'
           f'    "        \\"Inter-PADD movement: PADD {B} -> PADD {A}\\", '
           f'1.0, \\"mbbl\\", \\"auxiliary\\",\\n",\n')
    RAW_PATCHES.append((old, new, series_in))

# MCRMT* (tanker/barge) raw bindings: 2-line format with "P{A}->P{B}" description
# (without the "PADD" prefix). MCRMTP3P51 has a parenthetical "(Jones Act WB->WC)"
# which we deliberately leave alone — only the direction substring changes.
for series_in, A, B in [
    ("MCRMTP1P21", 1, 2), ("MCRMTP1P31", 1, 3),
    ("MCRMTP2P11", 2, 1), ("MCRMTP2P31", 2, 3),
    ("MCRMTP3P11", 3, 1), ("MCRMTP3P21", 3, 2),
    ("MCRMTP5P31", 5, 3),
]:
    old_first  = (f'    "    (\\"{series_in}\\", \\"eia:{series_in}\\", '
                  f'\\"padd{A}_view\\", \\"padd{B}_view\\", \\"outflow\\",\\n",')
    new_first  = (f'    "    (\\"{series_in}\\", \\"eia:{series_in}\\", '
                  f'\\"padd{B}_view\\", \\"padd{A}_view\\", \\"outflow\\",\\n",')
    old_descr  = f'P{A}->P{B}'
    new_descr  = f'P{B}->P{A}'
    RAW_PATCHES.append((old_first, new_first, f"{series_in} (line1)"))
    # Description swap on the trailing line — global within the cell is safe
    # since each direction substring is unique to its tuple.
    RAW_PATCHES.append((f'"Inter-PADD movement {old_descr}',
                        f'"Inter-PADD movement {new_descr}',
                        f"{series_in} (descr)"))

# MCRMTP3P51 special-case: only the line1 (node, related_node) swap is
# required for correctness. The description swap is cosmetic and skipped
# because the parenthetical "(Jones Act WB->WC)" makes it order-dependent
# with the bare loop swaps above; it can be cleaned up later by hand.
RAW_PATCHES.append((
    '    "    (\\"MCRMTP3P51\\", \\"eia:MCRMTP3P51\\", \\"padd3_view\\", \\"padd5_view\\", \\"outflow\\",\\n",',
    '    "    (\\"MCRMTP3P51\\", \\"eia:MCRMTP3P51\\", \\"padd5_view\\", \\"padd3_view\\", \\"outflow\\",\\n",',
    "MCRMTP3P51 (line1)",
))

# Region-coded raw bindings (R10-R30, R40-R30): both are P3 -> P{X} per EIA
# (PADD X receipts from PADD 3).
R_RAW_PATCHES = [
    # MCRMP_R10-R30_1 = P3 -> P1 ; was bound (padd1, padd3); should be (padd3, padd1)
    (
        '    "    (\\"MCRMP_R10-R30_1\\", \\"eia:MCRMP_R10-R30_1\\", \\"padd1_view\\", \\"padd3_view\\", \\"outflow\\",\\n",',
        '    "    (\\"MCRMP_R10-R30_1\\", \\"eia:MCRMP_R10-R30_1\\", \\"padd3_view\\", \\"padd1_view\\", \\"outflow\\",\\n",',
    ),
    # MCRMP_R40-R30_1 = P3 -> P4 ; was bound (padd4, padd3); should be (padd3, padd4)
    (
        '    "    (\\"MCRMP_R40-R30_1\\", \\"eia:MCRMP_R40-R30_1\\", \\"padd4_view\\", \\"padd3_view\\", \\"outflow\\",\\n",',
        '    "    (\\"MCRMP_R40-R30_1\\", \\"eia:MCRMP_R40-R30_1\\", \\"padd3_view\\", \\"padd4_view\\", \\"outflow\\",\\n",',
    ),
]

# -- 2. COMBINED_INTER_PADD: swap each corridor's component series with its --
#       reverse-direction equivalent.  Each entry is (old_line, new_line)
#       matching the literal .ipynb JSON.
COMBINED_PATCHES = [
    # P1 -> P2 corridor: was using MCRMPP1P21 (= P2->P1); switch to MCRMPP2P11 (= P1->P2)
    (
        '    "    (\\"P1\\", \\"P2\\", [\\"eia:MCRMPP1P21_kbd\\", \\"eia:MCRMTP1P21_kbd\\"],\\n",',
        '    "    (\\"P1\\", \\"P2\\", [\\"eia:MCRMPP2P11_kbd\\", \\"eia:MCRMTP2P11_kbd\\"],\\n",',
    ),
    # P1 -> P3: was using MCRMP_R10-R30_1 + MCRMTP1P31 (both P3->P1); switch to MCRMPP3P11 + MCRMTP3P11
    (
        '    "    (\\"P1\\", \\"P3\\", [\\"eia:MCRMP_R10-R30_1_kbd\\", \\"eia:MCRMTP1P31_kbd\\",\\n",',
        '    "    (\\"P1\\", \\"P3\\", [\\"eia:MCRMPP3P11_kbd\\", \\"eia:MCRMTP3P11_kbd\\",\\n",',
    ),
    # P2 -> P1: was MCRMPP2P11 (= P1->P2); switch to MCRMPP1P21 (= P2->P1)
    (
        '    "    (\\"P2\\", \\"P1\\", [\\"eia:MCRMPP2P11_kbd\\", \\"eia:MCRMTP2P11_kbd\\"],\\n",',
        '    "    (\\"P2\\", \\"P1\\", [\\"eia:MCRMPP1P21_kbd\\", \\"eia:MCRMTP1P21_kbd\\"],\\n",',
    ),
    # P2 -> P3: was MCRMPP2P31 (= P3->P2); switch to MCRMPP3P21 (= P2->P3)
    (
        '    "    (\\"P2\\", \\"P3\\", [\\"eia:MCRMPP2P31_kbd\\", \\"eia:MCRMTP2P31_kbd\\"],\\n",',
        '    "    (\\"P2\\", \\"P3\\", [\\"eia:MCRMPP3P21_kbd\\", \\"eia:MCRMTP3P21_kbd\\"],\\n",',
    ),
    # P2 -> P4: was MCRMPP2P41 (= P4->P2); switch to MCRMPP4P21 (= P2->P4)
    (
        '    "    (\\"P2\\", \\"P4\\", [\\"eia:MCRMPP2P41_kbd\\"],\\n",',
        '    "    (\\"P2\\", \\"P4\\", [\\"eia:MCRMPP4P21_kbd\\"],\\n",',
    ),
    # P3 -> P1: was MCRMPP3P11 + MCRMTP3P11 (= P1->P3); switch to MCRMP_R10-R30_1 + MCRMTP1P31 (= P3->P1)
    (
        '    "    (\\"P3\\", \\"P1\\", [\\"eia:MCRMPP3P11_kbd\\", \\"eia:MCRMTP3P11_kbd\\"],\\n",',
        '    "    (\\"P3\\", \\"P1\\", [\\"eia:MCRMP_R10-R30_1_kbd\\", \\"eia:MCRMTP1P31_kbd\\"],\\n",',
    ),
    # P3 -> P2: was MCRMPP3P21 + MCRMTP3P21 (= P2->P3); switch to MCRMPP2P31 + MCRMTP2P31 (= P3->P2)
    (
        '    "    (\\"P3\\", \\"P2\\", [\\"eia:MCRMPP3P21_kbd\\", \\"eia:MCRMTP3P21_kbd\\"],\\n",',
        '    "    (\\"P3\\", \\"P2\\", [\\"eia:MCRMPP2P31_kbd\\", \\"eia:MCRMTP2P31_kbd\\"],\\n",',
    ),
    # P3 -> P4: was MCRMPP3P41 (= P4->P3); switch to MCRMP_R40-R30_1 (= P3->P4)
    (
        '    "    (\\"P3\\", \\"P4\\", [\\"eia:MCRMPP3P41_kbd\\"],\\n",',
        '    "    (\\"P3\\", \\"P4\\", [\\"eia:MCRMP_R40-R30_1_kbd\\"],\\n",',
    ),
    # P3 -> P5: was MCRMTP3P51 (= P5->P3); switch to MCRMTP5P31 (= P3->P5)
    (
        '    "    (\\"P3\\", \\"P5\\", [\\"eia:MCRMTP3P51_kbd\\"],\\n",',
        '    "    (\\"P3\\", \\"P5\\", [\\"eia:MCRMTP5P31_kbd\\"],\\n",',
    ),
    # P4 -> P2: was MCRMPP4P21 (= P2->P4); switch to MCRMPP2P41 (= P4->P2)
    (
        '    "    (\\"P4\\", \\"P2\\", [\\"eia:MCRMPP4P21_kbd\\"],\\n",',
        '    "    (\\"P4\\", \\"P2\\", [\\"eia:MCRMPP2P41_kbd\\"],\\n",',
    ),
    # P4 -> P3: was MCRMP_R40-R30_1 (= P3->P4); switch to MCRMPP3P41 (= P4->P3)
    (
        '    "    (\\"P4\\", \\"P3\\", [\\"eia:MCRMP_R40-R30_1_kbd\\"],\\n",',
        '    "    (\\"P4\\", \\"P3\\", [\\"eia:MCRMPP3P41_kbd\\"],\\n",',
    ),
    # P5 -> P3: was MCRMTP5P31 (= P3->P5); switch to MCRMTP3P51 (= P5->P3)
    (
        '    "    (\\"P5\\", \\"P3\\", [\\"eia:MCRMTP5P31_kbd\\"],\\n",',
        '    "    (\\"P5\\", \\"P3\\", [\\"eia:MCRMTP3P51_kbd\\"],\\n",',
    ),
]


def main() -> None:
    text = NB.read_text(encoding="utf-8")
    log = []

    # COMBINED patches
    for old, new in COMBINED_PATCHES:
        if new in text and old not in text:
            log.append(f"  [already-done] COMBINED: {new[:80]}")
            continue
        if old not in text:
            log.append(f"  [MISSING]     COMBINED anchor not found: {old[:80]}")
            continue
        text = text.replace(old, new, 1)
        log.append(f"  [applied]     COMBINED: {new[:80]}")

    # Raw region-coded patches
    for old, new in R_RAW_PATCHES:
        if new in text and old not in text:
            log.append(f"  [already-done] RAW(R*): {new[:80]}")
            continue
        if old not in text:
            log.append(f"  [MISSING]     RAW(R*) anchor not found: {old[:80]}")
            continue
        text = text.replace(old, new, 1)
        log.append(f"  [applied]     RAW(R*): {new[:80]}")

    # Raw MCRMP* / MCRMT* patches (node/related_node + description swap)
    for old, new, series_in in RAW_PATCHES:
        if new in text and old not in text:
            log.append(f"  [already-done] RAW: {series_in}")
            continue
        if old not in text:
            log.append(f"  [MISSING]     RAW {series_in} anchor not found")
            continue
        text = text.replace(old, new, 1)
        log.append(f"  [applied]     RAW: {series_in}")

    print("\n".join(log))

    missing = [s for s in log if "[MISSING]" in s]
    if missing:
        print()
        print(f"ERROR: {len(missing)} anchors not found. Aborting without write.")
        sys.exit(1)

    NB.write_text(text, encoding="utf-8")
    print(f"\nWrote {NB}")


if __name__ == "__main__":
    main()
