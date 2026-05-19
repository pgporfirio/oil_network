"""Eighteenth pass — derive every structural relationship from the variables.

Audit found ~30 hardcoded (child, parent) rules in `make_hierarchy_explorer.py`
and `make_balance_ui.py`. Most duplicate what `v_partition_tree` already
captures; ~8 do not, because the affected nodes are constraint / observational
aggregates (basin views, state views, SPR total, USA subtotal) that sit
*outside* the partition spine by design.

This pass adds those 8 nodes as `inventory-children` of their natural
display parent — the same eleventh / seventeenth-pass pattern used for
gathering nodes and `pipe_bakken_xstate`. **Zero numerical effect**
(`inventory__crude__<constraint_node>` is `0` or `latent()` by node-type
default), but `v_partition_tree` now records the relation, so:

  * the hierarchy / balance HTMLs can read the partition tree as their
    single source of truth for hierarchical display
  * the renderers no longer need any hardcoded `structural` dict, no
    `EXPLICIT_PADD` map, no `derive_padd()` regex
  * adding a new constraint node is a one-line `formula_inputs` update,
    not a code change

Mappings declared:

    inventory__crude__usa_view  +=  [
        inventory__crude__permian,                      (basin rollup)
        inventory__crude__bakken,
        inventory__crude__eagle_ford,
        inventory__crude__spr_total,                    (SPR rollup)
        inventory__crude__usa_lower48_excl_gom_view,    (STEO subtotal)
    ]
    inventory__crude__padd3_view += [inventory__crude__texas_state_view]
    inventory__crude__padd4_view += [inventory__crude__montana_state_view]

Idempotent.
"""
from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"

# (parent_view, [list of constraint child nodes to add as inventory inputs])
MEMBERSHIP = [
    ("usa_view",   ["permian", "bakken", "eagle_ford",
                    "spr_total", "usa_lower48_excl_gom_view"]),
    ("padd3_view", ["texas_state_view"]),
    ("padd4_view", ["montana_state_view"]),
]


def add_membership(cur, parent_view: str, child_nodes: list[str]) -> int:
    """Add inventory__crude__<child> entries to the parent view's
    inventory.formula_inputs. Returns number of entries added."""
    inv_var = f"inventory__crude__{parent_view}"
    new_inputs = [f"inventory__crude__{c}" for c in child_nodes]
    cur.execute("""
        SELECT timeseries_id, formula, formula_inputs, notes
        FROM oil_network.variable_assignments
        WHERE scenario_id = %s AND variable_id = %s
    """, (SCENARIO, inv_var))
    row = cur.fetchone()
    if row is None:
        print(f"  ! no override row for {inv_var}; skipping")
        return 0
    ts, formula, existing_inputs, notes = row
    existing = list(existing_inputs or [])
    added = [x for x in new_inputs if x not in existing]
    if not added:
        print(f"  · {inv_var}: all already members")
        return 0
    merged = existing + added
    new_notes = (notes or "") + (
        " | eighteenth-pass: constraint nodes added as inventory-members "
        "(zero numerical effect; structural display only)"
    )
    cur.execute("""
        UPDATE oil_network.variable_assignments
        SET formula_inputs = %s, notes = %s
        WHERE scenario_id = %s AND variable_id = %s
    """, (merged, new_notes, SCENARIO, inv_var))
    print(f"  ✓ {inv_var}: +{added}")
    return len(added)


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        total = 0
        for parent_view, children in MEMBERSHIP:
            total += add_membership(cur, parent_view, children)
        conn.commit()
        print(f"\nTotal new inventory-member rows: {total}")
        print("Next: refresh_views.py --structural to rebuild v_partition_tree.")


if __name__ == "__main__":
    main()
