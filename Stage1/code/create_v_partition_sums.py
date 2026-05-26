"""Create v_partition_sums — pre-computed sum of typed partition children.

Same logic as `v_aggregation_consistency`'s `child_sum` CTE, but exposed for
every parent / variable_type / related_node / date so the balance UI can read
the sum directly rather than computing it in JavaScript at render time.

The previous JS `treeSumKids` walked `children_partition` = union across all
variable_types and summed every descendant's flow edges, classifying by
"other end in descendants". That over-counted boundary flows because the
inter-PADD aggregates already capture cross-PADD flows once; including
descendant refineries' edges from out-of-PADD hubs double-counted them.

This view enforces the strict partition discipline: for each (parent, vtype,
related_node), sum only the **type-specific** partition children — the same
ones `v_partition_tree` declares. Every aggregation has a unique decomposition
into its declared constituents.

For relational variables (inflow / outflow), the per-related_node sum is
preserved; a second row with `related_node_id IS NULL` provides the lumped
sum (across related_nodes) for the UI's lumped I/O cells.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_partition_sums CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_partition_sums AS
WITH typed_child_values AS (
    -- One row per (parent, vtype, related_node, child, date) with the child's value.
    SELECT pt.scenario_id, pt.parent_node_id, pt.variable_type, pt.commodity,
           pt.related_node_id, pt.child_node_id,
           srv.observation_date, srv.value, srv.source
      FROM oil_network.v_partition_tree pt
      JOIN oil_network.variables v
        ON v.node_id      = pt.child_node_id
       AND v.variable_type = pt.variable_type
       AND v.commodity     = pt.commodity
       AND COALESCE(v.related_node_id, '') = COALESCE(pt.related_node_id, '')
      JOIN oil_network.scenario_resolved_values srv
        ON srv.scenario_id = pt.scenario_id
       AND srv.variable_id = v.variable_id
),
-- Per-related_node sum (one row per typed partition slot)
per_related AS (
    SELECT scenario_id, parent_node_id, variable_type, commodity, related_node_id,
           observation_date,
           SUM(value) FILTER (WHERE value IS NOT NULL)
             AS sum_children,
           COUNT(*) AS n_children,
           COUNT(value) AS n_with_value
      FROM typed_child_values
     GROUP BY scenario_id, parent_node_id, variable_type, commodity, related_node_id,
              observation_date
),
-- Lumped (across related_nodes) — relevant for relational types where the UI
-- shows a single I or O cell per node, not one per (vtype, related_node).
lumped AS (
    SELECT scenario_id, parent_node_id, variable_type, commodity,
           NULL::TEXT AS related_node_id,
           observation_date,
           SUM(value) FILTER (WHERE value IS NOT NULL)
             AS sum_children,
           COUNT(*) AS n_children,
           COUNT(value) AS n_with_value
      FROM typed_child_values
     WHERE variable_type IN ('inflow', 'outflow')
     GROUP BY scenario_id, parent_node_id, variable_type, commodity, observation_date
)
SELECT * FROM per_related
UNION ALL
SELECT * FROM lumped
;

CREATE UNIQUE INDEX IF NOT EXISTS ix_vps_pk
  ON oil_network.v_partition_sums
     (scenario_id, parent_node_id, variable_type, commodity,
      COALESCE(related_node_id, '__lumped__'), observation_date);

-- Intra-partition flow magnitudes (informational, for the UI's "+N intra"
-- annotation). For a parent P, intra inflow at D is the sum of all inflow
-- values on descendants of P where the source (related_node) is ALSO a
-- descendant of P. Same for outflow. These flows don't appear at the
-- parent's boundary because they net out internally.
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_partition_intra_flows CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_partition_intra_flows AS
WITH RECURSIVE descendants AS (
    SELECT scenario_id, parent_node_id AS root, child_node_id AS node
      FROM oil_network.v_partition_tree
    UNION
    SELECT d.scenario_id, d.root, pt.child_node_id
      FROM descendants d
      JOIN oil_network.v_partition_tree pt
        ON pt.parent_node_id = d.node AND pt.scenario_id = d.scenario_id
)
SELECT d.scenario_id, d.root AS parent_node_id, v.variable_type, v.commodity,
       srv.observation_date,
       SUM(srv.value) FILTER (WHERE srv.value IS NOT NULL) AS intra_sum,
       COUNT(*) AS n_intra_edges
  FROM descendants d
  JOIN oil_network.variables v ON v.node_id = d.node
                              AND v.variable_type IN ('inflow', 'outflow')
                              AND v.related_node_id IS NOT NULL
  JOIN descendants d2 ON d2.scenario_id = d.scenario_id
                     AND d2.root = d.root
                     AND d2.node = v.related_node_id
  JOIN oil_network.scenario_resolved_values srv
        ON srv.scenario_id = d.scenario_id
       AND srv.variable_id = v.variable_id
 GROUP BY d.scenario_id, d.root, v.variable_type, v.commodity, srv.observation_date
;

CREATE UNIQUE INDEX IF NOT EXISTS ix_vpif_pk
  ON oil_network.v_partition_intra_flows
     (scenario_id, parent_node_id, variable_type, commodity, observation_date);
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("SELECT COUNT(*) FROM oil_network.v_partition_sums")
        n = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM oil_network.v_partition_intra_flows")
        m = cur.fetchone()[0]
        print(f"  v_partition_sums:        {n:,} rows")
        print(f"  v_partition_intra_flows: {m:,} rows")
        conn.commit()


if __name__ == "__main__":
    main()
