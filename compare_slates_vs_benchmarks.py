"""Diff the loaded per-(refinery, grade) slates against independent published benchmarks.

Inputs:
  - per_grade_db_aggregates.json
       Per-(commodity, main_group) DB aggregates: avg, min, max, stddev
       computed by dump_per_grade_aggregates.py from
       refineries.refinery_grade_slate.

  - config/per_grade_yield_benchmarks.json
       Published industry benchmarks per crude grade for typical
       cracking-refinery and coking-refinery yields, with range_min /
       range_max per main product group. Produced by a blank-slate
       research agent — independent of the DB.

For each (commodity, main_group), the DB average yield is compared
against the benchmark's "typical_cracking_refinery" range. Flags:

  - **out_of_range**: DB avg outside [range_min - 3, range_max + 3]
        (3-pct tolerance for noise from PADD-aware leaf allocation).
  - **borderline**:   DB avg within tolerance band but outside the
                       nominal [range_min, range_max].
  - **ok**:           DB avg inside the benchmark range.
  - **no_benchmark**: no benchmark row for this (commodity, main_group).

Writes a comparison JSON report alongside a console summary.

Run via:

    ..\\..\\.venv\\Scripts\\python.exe ..\\Stage2\\compare_slates_vs_benchmarks.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_FILE     = HERE / "per_grade_db_aggregates.json"
BENCH_FILE  = HERE / "config" / "per_grade_yield_benchmarks.json"
OUT_FILE    = HERE / "per_grade_comparison.json"

TOLERANCE_PCT = 3.0  # extra wiggle around the benchmark range


def main() -> None:
    if not DB_FILE.exists():
        sys.exit(f"missing {DB_FILE}; run dump_per_grade_aggregates.py first")
    if not BENCH_FILE.exists():
        sys.exit(f"missing {BENCH_FILE}; benchmark agent must complete first")

    db = json.loads(DB_FILE.read_text(encoding="utf-8"))
    bench = json.loads(BENCH_FILE.read_text(encoding="utf-8"))

    comparison: list[dict] = []
    flag_counts: dict[str, int] = {"ok": 0, "borderline": 0,
                                    "out_of_range": 0, "no_benchmark": 0}

    for commodity, groups in db["grades"].items():
        bench_entry = bench["grades"].get(commodity)
        bench_typical = (bench_entry or {}).get("typical_cracking_refinery") or {}
        bench_coking  = (bench_entry or {}).get("typical_coking_refinery") or {}

        for main_group, db_stats in groups.items():
            db_avg = db_stats["avg_yield_pct"]
            # For each main group, prefer "typical_coking" lower-bound and
            # "typical_cracking" upper-bound — most US refineries sit between
            # those two configurations, so the union is the meaningful range.
            range_min, range_max = None, None
            sources = []
            for label, table in (("cracking", bench_typical), ("coking", bench_coking)):
                if not table:
                    continue
                entry = table.get(main_group)
                if not entry:
                    continue
                rmin = entry.get("range_min")
                rmax = entry.get("range_max")
                if rmin is not None:
                    range_min = rmin if range_min is None else min(range_min, rmin)
                if rmax is not None:
                    range_max = rmax if range_max is None else max(range_max, rmax)
                sources.append(label)

            if range_min is None and range_max is None:
                flag = "no_benchmark"
            else:
                lo = (range_min or 0) - TOLERANCE_PCT
                hi = (range_max or 100) + TOLERANCE_PCT
                if range_min is not None and range_max is not None \
                        and range_min <= db_avg <= range_max:
                    flag = "ok"
                elif lo <= db_avg <= hi:
                    flag = "borderline"
                else:
                    flag = "out_of_range"

            flag_counts[flag] = flag_counts.get(flag, 0) + 1

            comparison.append({
                "commodity":      commodity,
                "main_group":     main_group,
                "n_refineries":   db_stats["n_refineries"],
                "db_avg":         db_avg,
                "db_min":         db_stats["min_yield_pct"],
                "db_max":         db_stats["max_yield_pct"],
                "bench_range_min": range_min,
                "bench_range_max": range_max,
                "bench_sources":  sources,
                "flag":           flag,
            })

    report = {
        "kind": "per_grade_db_vs_benchmark_diff",
        "tolerance_pct_outside_range": TOLERANCE_PCT,
        "summary_flags": flag_counts,
        "n_rows": len(comparison),
        "comparison": comparison,
    }
    OUT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"wrote {OUT_FILE}")
    print()
    print(f"Comparison summary ({len(comparison)} grade x main_group rows):")
    for f, n in flag_counts.items():
        print(f"  {f:<14} {n:>3}")

    print()
    print("Out-of-range rows (DB avg falls outside benchmark + tolerance):")
    print(f"  {'commodity':<22} {'main_group':<26} {'db_avg':>7} "
          f"{'min..max':>11}  bench")
    for r in comparison:
        if r["flag"] != "out_of_range":
            continue
        rng = f"[{r['bench_range_min']}, {r['bench_range_max']}]"
        print(f"  {r['commodity']:<22} {r['main_group']:<26} "
              f"{r['db_avg']:>7.2f} "
              f"({r['db_min']:>4.1f}..{r['db_max']:>5.1f})  {rng}")

    print()
    print("Borderline rows (DB avg outside nominal range but within tolerance):")
    n_print = 0
    for r in comparison:
        if r["flag"] != "borderline":
            continue
        if n_print >= 20:
            print(f"  ... and {sum(1 for x in comparison if x['flag']=='borderline') - 20} more")
            break
        rng = f"[{r['bench_range_min']}, {r['bench_range_max']}]"
        print(f"  {r['commodity']:<22} {r['main_group']:<26} "
              f"{r['db_avg']:>7.2f}  bench {rng}")
        n_print += 1


if __name__ == "__main__":
    main()
