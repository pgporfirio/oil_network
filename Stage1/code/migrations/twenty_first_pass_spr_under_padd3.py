"""Twenty-first pass: place SPR under PADD3 in the inventory partition.

The eighteenth pass added `inventory__crude__spr_total` to
`inventory__crude__usa_view.formula_inputs` for hierarchy display, expecting
zero numerical effect. But `spr_total` is TS-observed (EIA `MCSSTUS1`, ~394
MMbbl), and the SPR stocks are *already* included in both `MCRSTP3` and
`MCRSTUS1`. Adding spr_total as a sibling of the 5 PADDs under usa_view
produces a 394 MMbbl double-count that v_aggregation_consistency flagged
across 134 monthly rows.

Geographically all 4 SPR sites are in PADD3 (Texas/Louisiana). The cleanest
fix is to place `spr_total` as a structural child of `padd3_view` (where it
already lives physically), and re-parent the 4 SPR sites under `spr_total`.

Three edits on `variable_assignments.formula_inputs`:

  1. usa_view.inventory:
       remove  inventory__crude__spr_total

  2. padd3_view.inventory:
       remove  4 SPR site inventory variables (move down a level)
       add     inventory__crude__spr_total

  3. spr_total.inventory:
       add  4 SPR site inventory variables (declares structural decomposition;
            sites stay latent, so zero numerical effect — Principle 2.8 dual
            role: when parent is TS-bound, formula_inputs are the constraint
            set, not the sum operands).

Numerical effect at the partition level: USA-inventory gap drops from
-394 MMbbl to ~0 (5 PADDs sum exactly to MCRSTUS1); PADD3-inventory gap
stays at ~228 MMbbl (the commercial portion still latent — that's the
real partial_coverage gap, by design per Principle 2.11).

Idempotent. After running, re-run resolve_scenario.py + thirteenth-pass
view refresh + audits.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

SPR_SITES = [
    "inventory__crude__spr_bayou_choctaw",
    "inventory__crude__spr_big_hill",
    "inventory__crude__spr_bryan_mound",
    "inventory__crude__spr_west_hackberry",
]
SPR_TOTAL = "inventory__crude__spr_total"


def array_remove(cur, variable_id: str, items_to_remove: list[str]) -> int:
    """Remove items from formula_inputs of one variable_assignment row.
    Returns number of items actually removed."""
    cur.execute("""
        SELECT formula_inputs FROM oil_network.variable_assignments
        WHERE variable_id = %s AND scenario_id = %s
    """, (variable_id, SCENARIO))
    row = cur.fetchone()
    if not row or row[0] is None:
        return 0
    current = list(row[0])
    new = [x for x in current if x not in items_to_remove]
    removed = len(current) - len(new)
    if removed > 0:
        cur.execute("""
            UPDATE oil_network.variable_assignments
            SET formula_inputs = %s
            WHERE variable_id = %s AND scenario_id = %s
        """, (new, variable_id, SCENARIO))
    return removed


def array_add(cur, variable_id: str, items_to_add: list[str]) -> int:
    """Add items to formula_inputs of one variable_assignment row (deduped).
    Returns number of items actually added."""
    cur.execute("""
        SELECT formula_inputs FROM oil_network.variable_assignments
        WHERE variable_id = %s AND scenario_id = %s
    """, (variable_id, SCENARIO))
    row = cur.fetchone()
    if not row:
        return 0
    current = list(row[0] or [])
    to_add = [x for x in items_to_add if x not in current]
    if to_add:
        new = current + to_add
        cur.execute("""
            UPDATE oil_network.variable_assignments
            SET formula_inputs = %s
            WHERE variable_id = %s AND scenario_id = %s
        """, (new, variable_id, SCENARIO))
    return len(to_add)


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("=== Step 1: remove spr_total from usa_view.inventory ===")
        n = array_remove(cur, "inventory__crude__usa_view", [SPR_TOTAL])
        print(f"  removed {n}: {SPR_TOTAL}" if n else "  nothing to remove (idempotent re-run)")

        print()
        print("=== Step 2: padd3_view.inventory: remove 4 SPR sites, add spr_total ===")
        n_removed = array_remove(cur, "inventory__crude__padd3_view", SPR_SITES)
        print(f"  removed {n_removed} SPR site variables")
        n_added = array_add(cur, "inventory__crude__padd3_view", [SPR_TOTAL])
        print(f"  added: {SPR_TOTAL}" if n_added else "  spr_total already present (idempotent re-run)")

        print()
        print("=== Step 3: spr_total.inventory: add 4 SPR sites as constraint members ===")
        n_added = array_add(cur, "inventory__crude__spr_total", SPR_SITES)
        print(f"  added {n_added} SPR site variables to formula_inputs")

        conn.commit()
        print()
        print("Committed. Run refresh_views.py --structural, resolve_scenario.py, audits to verify.")


if __name__ == "__main__":
    main()
