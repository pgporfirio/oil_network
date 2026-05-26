"""Declare pipe_enbridge_mainline_ca and pipe_keystone as inventory members
of padd2_view, so they appear nested under PADD 2 in the partition tree
rather than as top-level orphans in the balance UI.

Both pipelines deliver Canadian crude into PADD 2:
- pipe_enbridge_mainline_ca: canadian_oil_sands -> clearbrook_entry (PADD-2 entry)
- pipe_keystone:             canadian_oil_sands -> cushing_hub      (PADD-2 hub)

The line-fill inventory on the modelled (US-facing) portion of each pipe
naturally sits in PADD 2 for balance purposes. This migration adds each
pipe's `inventory__crude__pipe_*` variable to `inventory__crude__padd2_view`'s
formula_inputs.

Numerical effect: zero. Both pipes carry `formula = '0'` (or `'latent()'`)
on their inventory variables today, so adding them to the sum changes
nothing in the resolved table.

Audit effect: the pipes now appear under PADD 2 in v_partition_tree, the
balance HTML nests them correctly, and the partition closure math is
preserved (sum of children still matches the published PADD 2 inventory).

Same pattern as the eleventh-pass gathering inventory membership and the
twenty-second pass refactor of Jones Act line-fill onto inter_padd_3_to_5_agg.
Idempotent — re-running is a no-op.
"""
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

ADDITIONS = [
    # (parent_inventory_variable_id, child_inventory_variable_id)
    ("inventory__crude__padd2_view", "inventory__crude__pipe_enbridge_mainline_ca"),
    ("inventory__crude__padd2_view", "inventory__crude__pipe_keystone"),
]


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        for parent_vid, child_vid in ADDITIONS:
            # Make sure the child variable actually exists
            cur.execute(
                "SELECT 1 FROM oil_network.variables WHERE variable_id = %s",
                (child_vid,),
            )
            if not cur.fetchone():
                print(f"  SKIP: child variable {child_vid} does not exist")
                continue

            cur.execute(
                """SELECT formula_inputs FROM oil_network.variable_assignments
                   WHERE variable_id = %s""",
                (parent_vid,),
            )
            row = cur.fetchone()
            if not row:
                print(f"  SKIP: parent assignment for {parent_vid} not found")
                continue
            current = list(row[0] or [])
            if child_vid in current:
                print(f"  SKIP: {child_vid} already in {parent_vid}.formula_inputs")
                continue
            new_inputs = current + [child_vid]
            cur.execute(
                """UPDATE oil_network.variable_assignments
                   SET formula_inputs = %s WHERE variable_id = %s""",
                (new_inputs, parent_vid),
            )
            print(f"  ADDED: {child_vid} -> {parent_vid}.formula_inputs "
                  f"(now {len(new_inputs)} inputs)")
        conn.commit()
    print("Done.")


if __name__ == "__main__":
    main()
