"""Cell-by-cell verification: for every partition parent, check whether
each cell (P, C, I, O, B, S, dS) closes against Sum of children.

For each (parent, variable_type), compares:
  parent_value  --- the resolved value of parent.<variable_type>
  children_sum  --- sum of children's same-type values
  gap = parent - children_sum

Reports every gap, classified:
  - exact:           |gap| < 0.5 kbd
  - rounding:        0.5 <= |gap| < 5 kbd
  - notable:         5 <= |gap| < 100 kbd
  - significant:     |gap| >= 100 kbd

Then walks each significant gap and tries to explain it (partial-coverage,
latent child, type-specific behaviour).
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from collections import defaultdict
from network_graph import NetworkGraph

SCENARIO = "starter_us_crude_2015_2025"
DATE = "2024-12-01"

VARIABLE_TYPES = ["production", "consumption", "inventory", "balancing_item"]
# Note: inflow / outflow per-edge is handled separately by audit_partition_gaps.


def classify(gap: float) -> str:
    a = abs(gap)
    if a < 0.5:  return "exact"
    if a < 5:    return "rounding"
    if a < 100:  return "notable"
    return "significant"


def main():
    g = NetworkGraph(SCENARIO)
    print(f"Loaded: {g}\n")

    # For each (parent, variable_type) where the parent has same-type children,
    # compare parent value with Σ children values.
    results = []
    counts = defaultdict(int)

    for parent, children_typed in [
        ((p, vt), kids)
        for (p, vt), kids in g._partition_children_typed.items()
        if vt in VARIABLE_TYPES
    ]:
        parent_node, vt = parent
        kids = list(children_typed)
        parent_var = g.node_variable_id(parent_node, vt)
        p_val = g.value(parent_var, DATE)
        if p_val is None:
            counts["parent_null"] += 1
            continue

        child_vals = []
        latent_kids = []
        for c in kids:
            cv_id = g.node_variable_id(c, vt)
            cv = g.value(cv_id, DATE)
            if cv is None:
                latent_kids.append(c)
            else:
                child_vals.append((c, cv))

        ch_sum = sum(v for _, v in child_vals)
        gap = p_val - ch_sum
        cls = classify(gap)
        counts[cls] += 1
        results.append({
            "parent": parent_node, "vt": vt,
            "p_val": p_val, "ch_sum": ch_sum, "gap": gap, "class": cls,
            "n_kids": len(kids), "n_latent_kids": len(latent_kids),
            "latent_kids": latent_kids,
            "children": child_vals,
        })

    # --- Summary ---
    print("=== Summary by gap class ===")
    for k in ("exact", "rounding", "notable", "significant", "parent_null"):
        print(f"  {k:14s} {counts[k]:>4d}")
    print()

    # --- Detail for non-exact gaps ---
    print("=== Non-exact gaps (rounding + notable + significant) ===")
    notable_rows = [r for r in results if r["class"] != "exact"]
    notable_rows.sort(key=lambda r: -abs(r["gap"]))
    for r in notable_rows:
        marker = {"rounding": "·", "notable": "!", "significant": "!!"}[r["class"]]
        print(f"\n  {marker} {r['parent']:30s}.{r['vt']:14s}  "
              f"parent={r['p_val']:>8.1f}  SumKids={r['ch_sum']:>8.1f}  "
              f"gap={r['gap']:>7.2f} kbd  [{r['class']}]")
        if r["latent_kids"]:
            print(f"     latent children ({len(r['latent_kids'])}): "
                  f"{', '.join(r['latent_kids'][:5])}"
                  + (" ..." if len(r['latent_kids']) > 5 else ""))
        else:
            for c, v in sorted(r["children"], key=lambda x: -x[1])[:6]:
                print(f"     {c:35s} = {v:>8.1f}")


if __name__ == "__main__":
    main()
