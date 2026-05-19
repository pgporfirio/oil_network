"""Promote spr_total from 'constraint' role to 'balance' role.

After twenty_first_pass_spr_under_padd3.py declared spr_total as a structural
inventory_input of padd3_view, spr_total IS a partition cell — not an
auxiliary observation. Its scenario_node_role should reflect that.

Currently `spr_total.role = 'constraint'`, which causes the balance HTML
renderer to filter it OUT of padd3_view's children and show it as a
top-level Constraints item. With role = 'balance' it becomes a normal
partition child: padd3_view drill-down -> spr_total -> 4 SPR sites.

Idempotent. One row UPDATE.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE oil_network.scenario_node_role
            SET role = 'balance',
                notes = 'SPR aggregate; inventory partition cell of padd3_view '
                        '(declared as inventory_input in 21st-pass migration).'
            WHERE scenario_id = %s AND node_id = 'spr_total'
            RETURNING role
        """, (SCENARIO,))
        r = cur.fetchone()
        if r:
            print(f"  spr_total.role -> {r[0]}")
        else:
            print("  spr_total not found in scenario_node_role (idempotent re-run, may need INSERT)")
            cur.execute("""
                INSERT INTO oil_network.scenario_node_role(scenario_id, node_id, role, notes)
                VALUES (%s, 'spr_total', 'balance',
                        'SPR aggregate; partition cell of padd3_view (21st-pass declared inventory_input).')
                ON CONFLICT (scenario_id, node_id) DO UPDATE
                    SET role = EXCLUDED.role,
                        notes = EXCLUDED.notes
            """, (SCENARIO,))
            print("  inserted/updated spr_total role")

        conn.commit()
        print("Done. Re-run regenerate_htmls.py to refresh balance UI.")


if __name__ == "__main__":
    main()
