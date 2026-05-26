"""Merge algorithmic baseline + web-research overrides into the loadable seed.

Inputs:
  - config/refinery_grade_slates_baseline.json
       Algorithmic baseline; one entry per refinery, each carrying
       per-grade `products` arrays with leaf-level yield_pct rows.
  - config/refinery_grade_slates_groupA_overrides.json
  - config/refinery_grade_slates_groupB_overrides.json
  - config/refinery_grade_slates_groupC_overrides.json
       Agent output; each carries per-refinery `overrides_applied` rows
       with main-group totals plus source / notes.

Output:
  - config/refinery_grade_slates.json
       The merged seed. Same shape as the baseline (per-refinery, per-grade,
       leaf-level rows). Rows from refineries with web overrides have their
       baseline replaced by web-derived leaf yields (computed by applying
       the same per-PADD leaf allocation to the override's main-group
       totals). Source is then `refined_by_web_override`.

This file plus the three group overrides constitute the audit trail. The
baseline file is preserved untouched.

One-shot — kept under the project root for transparency.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "code"))

from paths import CONFIG_DIR  # noqa: E402
from build_refinery_slates import LEAF_ALLOCATIONS, allocate_leaf  # noqa: E402

BASELINE = CONFIG_DIR / "refinery_grade_slates_baseline.json"
SEED     = CONFIG_DIR / "refinery_grade_slates.json"
GROUPS = [
    CONFIG_DIR / "refinery_grade_slates_groupA_overrides.json",
    CONFIG_DIR / "refinery_grade_slates_groupB_overrides.json",
    CONFIG_DIR / "refinery_grade_slates_groupC_overrides.json",
]


def apply_override(main_group_yields: dict[str, float], padd: str | None,
                   source: str, source_url: str | None, notes: str | None
                   ) -> list[dict]:
    """Convert main-group override totals into leaf-level product rows.

    Applies the same PADD-aware leaf allocation used by the baseline
    generator, then normalises to 100. Returns the row list.
    """
    leaf: dict[str, float] = {}
    for group, amount in main_group_yields.items():
        for k, v in allocate_leaf(group, padd, amount).items():
            leaf[k] = leaf.get(k, 0.0) + v
    total = sum(leaf.values())
    if total == 0:
        return []
    leaf = {k: v * 100.0 / total for k, v in leaf.items() if v >= 0.05}
    total = sum(leaf.values())
    leaf = {k: round(v * 100.0 / total, 3) for k, v in leaf.items()}

    note_pieces = [f"source={source}"]
    if source_url:
        note_pieces.append(f"url={source_url}")
    if notes:
        note_pieces.append(notes)
    full_notes = "; ".join(note_pieces)

    return [
        {
            "product_code": p,
            "yield_pct":    y,
            "source":       "refined_by_web_override",
            "notes":        full_notes,
        }
        for p, y in sorted(leaf.items(), key=lambda kv: -kv[1])
    ]


def main() -> None:
    with open(BASELINE, encoding="utf-8") as f:
        seed = json.load(f)

    # PADD lookup for the override re-allocation
    padd_by_id = {r["refinery_id"]: r["context"].get("padd") for r in seed["refineries"]}
    # Refinery entry index
    idx = {r["refinery_id"]: i for i, r in enumerate(seed["refineries"])}

    n_overrides_total = 0
    n_overrides_missing_refinery = 0
    n_overrides_missing_grade = 0
    n_overrides_dropped_foreign = 0

    WILDCARD_KEYS = {"*", "all", "all_grades", "any"}
    # Commodity codes the agents used to denote foreign grades not in the
    # 23-grade vocabulary. Override is dropped with a count for the audit.
    FOREIGN_PLACEHOLDERS = {"wcs", "maya", "merey", "vasconia", "castilla",
                            "arab_medium", "arab_heavy"}

    for path in GROUPS:
        if not path.exists():
            print(f"[!] missing override file: {path.name}")
            continue
        with open(path, encoding="utf-8") as f:
            grp = json.load(f)
        n_this = 0
        for entry in grp.get("refineries", []):
            rid = entry["refinery_id"]
            if rid not in idx:
                print(f"[!] unknown refinery_id in {path.name}: {rid}")
                continue
            seed_entry = seed["refineries"][idx[rid]]
            # Index seed grades by commodity for in-place replacement
            grades_by_comm = {g["commodity"]: g for g in seed_entry["grades"]}
            for ov in entry.get("overrides_applied", []):
                comm = ov["commodity"]
                if comm in FOREIGN_PLACEHOLDERS:
                    n_overrides_dropped_foreign += 1
                    continue

                # Resolve target commodity list:
                #   - wildcard ("*", "all", etc.) -> every grade the refinery has
                #   - specific commodity -> just that one
                if comm in WILDCARD_KEYS:
                    targets = list(grades_by_comm.keys())
                    if not targets:
                        n_overrides_missing_grade += 1
                        continue
                else:
                    if comm not in grades_by_comm:
                        n_overrides_missing_grade += 1
                        print(f"[!] {rid}: override for {comm!r} but no baseline pair")
                        continue
                    targets = [comm]

                rows = apply_override(
                    main_group_yields=ov["main_group_yields"],
                    padd=padd_by_id.get(rid),
                    source=ov.get("source", "web_research"),
                    source_url=ov.get("source_url"),
                    notes=ov.get("notes"),
                )
                for t in targets:
                    grades_by_comm[t]["products"] = rows
                    n_this += 1
        print(f"[ok] {path.name}: merged {n_this} (refinery, grade) overrides "
              f"(group={grp.get('group')})")
        n_overrides_total += n_this

    # Tally counts
    n_pairs = sum(len(r["grades"]) for r in seed["refineries"])
    n_rows = sum(len(g["products"]) for r in seed["refineries"] for g in r["grades"])
    n_pairs_overridden = sum(
        1 for r in seed["refineries"] for g in r["grades"]
        if g["products"] and g["products"][0]["source"] == "refined_by_web_override"
    )
    n_pairs_baseline = n_pairs - n_pairs_overridden

    seed["kind"] = "merged_baseline_and_overrides"
    seed["n_pairs"] = n_pairs
    seed["n_product_rows"] = n_rows
    seed["n_pairs_overridden"] = n_pairs_overridden
    seed["n_pairs_baseline_only"] = n_pairs_baseline

    with open(SEED, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)

    print()
    print(f"Merged: {n_overrides_total} (refinery, grade) overrides applied")
    print(f"Final seed: {n_pairs} pairs, {n_rows} product rows")
    print(f"  overridden by web research: {n_pairs_overridden}")
    print(f"  algorithmic baseline only:  {n_pairs_baseline}")
    print(f"  unknown-grade override drops: {n_overrides_missing_grade}")
    print(f"  unknown-refinery override drops: {n_overrides_missing_refinery}")
    print(f"  foreign-grade override drops (WCS / Maya / Merey etc.): "
          f"{n_overrides_dropped_foreign}")
    print(f"\nWrote {SEED}")


if __name__ == "__main__":
    main()
