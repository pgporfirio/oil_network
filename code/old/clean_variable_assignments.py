"""Remove redundant rows from variable_assignments — rows whose formula now
matches the node_type default in node_type_default_formulas.

Once node_type_default_formulas was populated, every "boilerplate" row that
assign_formulas.ipynb wrote (formula='0' or 'latent()' that just matches the
type default) became dead weight: v_effective_assignments fills them from the
default layer anyway. This script removes them so:

  - variable_assignments shows only intentional overrides
  - new pass-through B=0 defaults take effect (otherwise the old 'latent()'
    overrides at permian_tx_gathering, etc. would mask the new default)

Two deletion passes:

  pass 1 — formula exactly matches the type default → row is dead weight
           (the view layer would have produced the same recipe).

  pass 2 — formula='latent()' AND default='0' → the row is MASKING the new
           structural-zero default (e.g. assign_formulas wrote
           balancing_item__crude__permian_tx_gathering=latent() before the
           pass-through B=0 default existed). Delete so the default takes
           effect and closes mass balance at pass-throughs.

Keeps:
  - rows with timeseries_id NOT NULL (real TS bindings)
  - rows whose formula='0' but default='latent()' (deliberate coverage-contract
    decisions: e.g. refinery.inventory=0 in starter scenario collapses per-asset
    stocks to PADD-level resolution; the structural default keeps them latent
    for future scenarios with per-asset data)
  - rows with non-empty formula_inputs (sum_over_children, arithmetic, etc.)
  - rows on variables whose node_type has no entry in node_type_default_formulas

Idempotent. Reports counts before/after.
"""
from __future__ import annotations

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


SQL_BEFORE_COUNTS = """
SELECT scenario_id, COUNT(*) AS n_rows,
       COUNT(*) FILTER (WHERE timeseries_id IS NOT NULL) AS ts_bound,
       COUNT(*) FILTER (WHERE formula = '0')             AS zero_rows,
       COUNT(*) FILTER (WHERE formula = 'latent()')      AS latent_rows
FROM oil_network.variable_assignments
GROUP BY scenario_id
ORDER BY scenario_id;
"""

# Pass 1: formula exactly matches the type default → row is dead weight.
SQL_DELETE_REDUNDANT = """
WITH redundant AS (
    SELECT va.scenario_id, va.variable_id, va.effective_from
    FROM oil_network.variable_assignments va
    JOIN oil_network.variables v       ON v.variable_id = va.variable_id
    JOIN oil_network.nodes n           ON n.node_id = v.node_id
    JOIN oil_network.node_type_default_formulas ntdf
         ON ntdf.node_type     = n.node_type
        AND ntdf.variable_type = v.variable_type
        AND (ntdf.commodity IS NULL OR ntdf.commodity = v.commodity)
    WHERE va.timeseries_id IS NULL
      AND va.formula = ntdf.default_formula
      AND COALESCE(array_length(va.formula_inputs, 1), 0) = 0
)
DELETE FROM oil_network.variable_assignments va
USING redundant r
WHERE va.scenario_id    = r.scenario_id
  AND va.variable_id    = r.variable_id
  AND va.effective_from = r.effective_from
RETURNING va.scenario_id, va.variable_id, va.formula;
"""

# Pass 2: formula='latent()' AND default='0' → row is MASKING the new
# structural-zero default (which is what activates pass-through mass balance).
SQL_DELETE_MASKING_LATENTS = """
WITH masking AS (
    SELECT va.scenario_id, va.variable_id, va.effective_from
    FROM oil_network.variable_assignments va
    JOIN oil_network.variables v       ON v.variable_id = va.variable_id
    JOIN oil_network.nodes n           ON n.node_id = v.node_id
    JOIN oil_network.node_type_default_formulas ntdf
         ON ntdf.node_type     = n.node_type
        AND ntdf.variable_type = v.variable_type
        AND (ntdf.commodity IS NULL OR ntdf.commodity = v.commodity)
    WHERE va.timeseries_id IS NULL
      AND va.formula = 'latent()'
      AND ntdf.default_formula = '0'
      AND COALESCE(array_length(va.formula_inputs, 1), 0) = 0
)
DELETE FROM oil_network.variable_assignments va
USING masking r
WHERE va.scenario_id    = r.scenario_id
  AND va.variable_id    = r.variable_id
  AND va.effective_from = r.effective_from
RETURNING va.scenario_id, va.variable_id, va.formula;
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("BEFORE cleanup:")
        cur.execute(SQL_BEFORE_COUNTS)
        for row in cur.fetchall():
            print(f"  {row[0]:<35}  total={row[1]}  ts={row[2]}  zero={row[3]}  latent={row[4]}")

        cur.execute(SQL_DELETE_REDUNDANT)
        deleted_pass1 = cur.fetchall()
        conn.commit()
        print(f"\nPass 1 — deleted {len(deleted_pass1)} rows where formula already matched the type default.")

        cur.execute(SQL_DELETE_MASKING_LATENTS)
        deleted_pass2 = cur.fetchall()
        conn.commit()
        print(f"Pass 2 — deleted {len(deleted_pass2)} masking 'latent()' rows where the new default is '0' "
              "(activates pass-through mass balance).")

        print("\nAFTER cleanup:")
        cur.execute(SQL_BEFORE_COUNTS)
        for row in cur.fetchall():
            print(f"  {row[0]:<35}  total={row[1]}  ts={row[2]}  zero={row[3]}  latent={row[4]}")

        # Re-check the view coverage now
        cur.execute("""
            SELECT assignment_source, COUNT(*) AS n
            FROM oil_network.v_effective_assignments
            WHERE scenario_id = 'starter_us_crude_2015_2025'
            GROUP BY assignment_source
            ORDER BY assignment_source
        """)
        print("\nv_effective_assignments coverage after cleanup (starter scenario):")
        for src, n in cur.fetchall():
            print(f"  {src:<10}  {n:>5} rows")


if __name__ == "__main__":
    main()
