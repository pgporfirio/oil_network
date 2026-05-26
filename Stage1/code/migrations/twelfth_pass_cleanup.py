"""Twelfth-pass framework cleanup.

Three observations Pedro flagged compound into one cleanup pass:

  (a) The "coverage contract" concept is conceptual sugar over
      `variable_assignments` per scenario. variable_assignments already
      encodes per-scenario authoritative/derived/collapsed. No separate
      contract object is needed.

  (b) The partition hierarchy is a derived view of `formula_inputs` with
      a same-type filter. It is currently re-implemented in inline SQL by
      `audit_partition_gaps.py` and `make_partition_map.py`. Promote it
      to a database view.

  (c) Node "status" (authoritative / derived / collapsed) is a derived
      property of a node's variable_assignments under a scenario. The
      `starter_status` column duplicates state that variable_assignments
      already encodes. Promote it to a view.

  (d) `sum_over_children`, `sum_over_outflows`, and `sum_same_type` are
      three resolver labels for one operation: sum the variables listed
      in `formula_inputs`. Collapse to one canonical formula text 'sum'.

  (e) Several JSONB columns duplicate first-class typed columns
      (`assets.attributes.kind/node_class/node_subtype/starter_status`,
       `nodes.attributes.starter_status`). Strip the duplicate keys
       from JSONB; the typed columns remain the source of truth.

This script applies (b), (c), (d), (e) as schema/data changes. (a) is
already true at the implementation level; the design docs and PDFs are
updated separately to reflect that.

Idempotent: views are CREATE OR REPLACE; data migrations check before
updating; JSONB key strips use the `-` operator which is a no-op on
absent keys.
"""
from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# ===========================================================================
# Step 1 — v_partition_tree
# ===========================================================================

V_PARTITION_TREE = """
CREATE OR REPLACE VIEW oil_network.v_partition_tree AS
SELECT va.scenario_id,
       v_parent.node_id        AS parent_node_id,
       v_child.node_id         AS child_node_id,
       v_parent.variable_type,
       v_parent.commodity,
       v_parent.related_node_id
FROM       oil_network.v_effective_assignments va
JOIN       oil_network.variables v_parent ON v_parent.variable_id = va.variable_id
CROSS JOIN LATERAL unnest(va.formula_inputs) AS inp(input_var)
JOIN       oil_network.variables v_child  ON v_child.variable_id = inp.input_var
WHERE v_parent.node_id <> v_child.node_id
  AND v_parent.variable_type = v_child.variable_type
  AND v_parent.commodity     = v_child.commodity
  AND COALESCE(v_parent.related_node_id,'') = COALESCE(v_child.related_node_id,'')
GROUP BY va.scenario_id, v_parent.node_id, v_child.node_id,
         v_parent.variable_type, v_parent.commodity, v_parent.related_node_id;

COMMENT ON VIEW oil_network.v_partition_tree IS
'Same-type aggregation hierarchy derived from formula_inputs. parent_node is '
'the partition parent of child_node when some variable on parent has '
'formula_inputs referencing a same-type, same-commodity, same-related-node '
'variable on child. Replaces the inline SQL that previously lived in '
'audit_partition_gaps.py and make_partition_map.py.';
"""


# ===========================================================================
# Step 2 — v_node_status
# ===========================================================================

V_NODE_STATUS = """
CREATE OR REPLACE VIEW oil_network.v_node_status AS
SELECT va.scenario_id,
       v.node_id,
       COUNT(*) FILTER (WHERE va.timeseries_id IS NOT NULL)                AS n_ts,
       COUNT(*) FILTER (WHERE va.formula = '0')                            AS n_zero,
       COUNT(*) FILTER (WHERE va.formula = 'latent()')                     AS n_latent,
       COUNT(*) FILTER (WHERE va.timeseries_id IS NULL
                          AND va.formula IS NOT NULL
                          AND va.formula NOT IN ('','0','latent()'))       AS n_derived,
       CASE
         WHEN COUNT(*) FILTER (WHERE va.timeseries_id IS NOT NULL) > 0
              THEN 'authoritative'
         WHEN COUNT(*) FILTER (WHERE va.timeseries_id IS NULL
                                 AND va.formula IS NOT NULL
                                 AND va.formula NOT IN ('','0','latent()')) > 0
              THEN 'derived'
         ELSE 'collapsed'
       END AS status
FROM oil_network.v_effective_assignments va
JOIN oil_network.variables v ON v.variable_id = va.variable_id
GROUP BY va.scenario_id, v.node_id;

COMMENT ON VIEW oil_network.v_node_status IS
'Inferred node status per (scenario, node). authoritative when any variable '
'is TS-bound; derived when any non-trivial formula is set; collapsed when all '
'variables are zero or latent. Supersedes the assets.starter_status / '
'nodes.starter_status columns, which carried hand-maintained duplicates.';
"""


# ===========================================================================
# Step 3 — Migrate sum_over_* / sum_same_type → 'sum'
# ===========================================================================

MIGRATE_SUM_FORMULAS = """
UPDATE oil_network.variable_assignments
SET formula = 'sum',
    notes   = COALESCE(NULLIF(notes,''),'')
              || CASE WHEN COALESCE(notes,'')='' THEN '' ELSE ' | ' END
              || 'twelfth-pass: formula collapsed from '
              || formula || ' to canonical sum (operands in formula_inputs)'
WHERE formula IN ('sum_over_children', 'sum_over_outflows', 'sum_same_type')
RETURNING variable_id;
"""


# ===========================================================================
# Step 4 — Strip duplicate JSONB keys
# ===========================================================================

STRIP_ASSETS_JSONB = """
UPDATE oil_network.assets
SET attributes = attributes - 'kind' - 'node_class' - 'node_subtype' - 'starter_status'
WHERE attributes ?| ARRAY['kind','node_class','node_subtype','starter_status']
RETURNING asset_id;
"""

STRIP_NODES_JSONB = """
UPDATE oil_network.nodes
SET attributes = attributes - 'starter_status'
WHERE attributes ? 'starter_status'
RETURNING node_id;
"""

STRIP_LOCATIONS_JSONB = """
-- locations.attributes carries 4 keys but all values are NULL in every row.
-- Strip them; the columns themselves either don't exist as typed columns
-- (so they'd be promoted later if needed) or are presently unused.
UPDATE oil_network.locations
SET attributes = attributes
                 - 'endpoint_outflow_refs'
                 - 'endpoint_intake_refs'
                 - 'spans_multiple_padds'
                 - 'padd_list'
WHERE attributes ?| ARRAY['endpoint_outflow_refs','endpoint_intake_refs',
                          'spans_multiple_padds','padd_list']
RETURNING location_id;
"""


# ===========================================================================
# Main
# ===========================================================================

def main():
    # Steps 1 and 2 (v_partition_tree, v_node_status as regular views) were
    # retired in favour of thirteenth_pass_views.py, which owns these views
    # as materialised. Running them here would conflict with the matviews
    # on standalone re-runs. The remaining work is the label migration
    # (now a defensive no-op since assign_formulas writes canonical 'sum'
    # directly) and the attributes-JSONB cleanup (still load-bearing —
    # load_asset_graph re-introduces the duplicate keys on every fresh
    # rebuild).
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("=== Step 3: Migrate sum_over_* / sum_same_type → 'sum' ===")
        cur.execute(MIGRATE_SUM_FORMULAS)
        rows = cur.fetchall()
        print(f"  ✓ {len(rows)} variable_assignments rows migrated:")
        for r in rows:
            print(f"    {r[0]}")
        print()

        print("=== Step 4a: Strip duplicate keys from assets.attributes ===")
        cur.execute(STRIP_ASSETS_JSONB)
        print(f"  ✓ {cur.rowcount} rows updated "
              "(removed kind/node_class/node_subtype/starter_status keys)")
        print()

        print("=== Step 4b: Strip starter_status from nodes.attributes ===")
        cur.execute(STRIP_NODES_JSONB)
        print(f"  ✓ {cur.rowcount} rows updated")
        print()

        print("=== Step 4c: Strip empty keys from locations.attributes ===")
        cur.execute(STRIP_LOCATIONS_JSONB)
        print(f"  ✓ {cur.rowcount} rows updated")
        print()

        conn.commit()
        print("All steps committed. Re-run resolve_scenario.py + audits to verify.")


if __name__ == "__main__":
    main()
