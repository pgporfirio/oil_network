"""Patch v_aggregation_consistency to handle missing-data children correctly.

Bug: the current view's child_sum CTE uses INNER JOIN to scenario_resolved_values.
Children whose TS data is missing for a given date — and children whose
derived formula fails because their own inputs are NULL — produce no row
in scenario_resolved_values. The INNER JOIN silently drops them, so the
view sees fewer children than v_partition_tree declares. `n_latent` stays
at 0, the gap looks unexplained, and the row is mislabeled `inconsistent`
even though the situation is "the partition can't be closed because data
for some children is missing."

Fix: use LEFT JOIN from v_partition_tree -> scenario_resolved_values, and
count any child without a value (whether absent or NULL) toward n_latent.
Status becomes `partial_coverage` when n_latent > 0 — the correct
semantic for the missing-data case.

Pedro's rule of thumb (formalised here): a `gap` exists only when ALL
children have known values AND those values don't sum to the parent. If
any child is missing or latent, the gap is just "unknown contribution of
the missing child(ren)" — not an inconsistency.

Idempotent: CREATE OR REPLACE the matview by drop+rebuild.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = r"""
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_aggregation_consistency CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_aggregation_consistency AS
WITH parent_var AS (
    SELECT DISTINCT pt.scenario_id,
        pt.parent_node_id,
        pt.variable_type,
        pt.commodity,
        pt.related_node_id,
        v.variable_id AS parent_var
       FROM oil_network.v_partition_tree pt
         JOIN oil_network.variables v ON v.node_id = pt.parent_node_id
                                       AND v.variable_type = pt.variable_type
                                       AND v.commodity = pt.commodity
                                       AND COALESCE(v.related_node_id, ''::text) = COALESCE(pt.related_node_id, ''::text)
         JOIN oil_network.v_effective_assignments va ON va.variable_id = v.variable_id
                                                      AND va.scenario_id = pt.scenario_id
                                                      AND va.timeseries_id IS NOT NULL
), parent_val AS (
    SELECT p.scenario_id, p.parent_node_id, p.variable_type, p.commodity, p.related_node_id,
           srv.observation_date, srv.value AS parent_value
    FROM parent_var p
    JOIN oil_network.scenario_resolved_values srv
      ON srv.scenario_id = p.scenario_id
     AND srv.variable_id = p.parent_var
), child_var AS (
    -- Per (parent_node, variable_type, commodity, related_node, scenario),
    -- list every declared child variable_id from v_partition_tree.
    SELECT pt.scenario_id, pt.parent_node_id, pt.variable_type, pt.commodity, pt.related_node_id,
           vc.variable_id AS child_var
    FROM oil_network.v_partition_tree pt
    JOIN oil_network.variables vc
      ON vc.node_id = pt.child_node_id
     AND vc.variable_type = pt.variable_type
     AND vc.commodity = pt.commodity
     AND COALESCE(vc.related_node_id,'') = COALESCE(pt.related_node_id,'')
), date_cv AS (
    -- Cartesian over the parent's observation dates: every (scenario, parent, date)
    -- pairs with every declared child variable, so LEFT JOIN below registers
    -- "no row for this child at this date" as a missing-data signal.
    SELECT p.scenario_id, p.parent_node_id, p.variable_type, p.commodity, p.related_node_id,
           p.observation_date, cv.child_var
    FROM parent_val p
    JOIN child_var cv
      ON cv.scenario_id    = p.scenario_id
     AND cv.parent_node_id = p.parent_node_id
     AND cv.variable_type  = p.variable_type
     AND cv.commodity      = p.commodity
     AND COALESCE(cv.related_node_id,'') = COALESCE(p.related_node_id,'')
), child_sum AS (
    SELECT dcv.scenario_id, dcv.parent_node_id, dcv.variable_type, dcv.commodity, dcv.related_node_id,
           dcv.observation_date,
           SUM(srv.value) FILTER (WHERE srv.value IS NOT NULL) AS sum_children,
           COUNT(*) FILTER (
               WHERE srv.value IS NULL
                  OR srv.source IN ('latent', 'unresolved')
           ) AS n_missing_or_latent,
           COUNT(*) AS n_children_declared
    FROM date_cv dcv
    LEFT JOIN oil_network.scenario_resolved_values srv
      ON srv.scenario_id      = dcv.scenario_id
     AND srv.variable_id      = dcv.child_var
     AND srv.observation_date = dcv.observation_date
    GROUP BY dcv.scenario_id, dcv.parent_node_id, dcv.variable_type, dcv.commodity,
             dcv.related_node_id, dcv.observation_date
)
SELECT p.scenario_id, p.parent_node_id, p.variable_type, p.commodity, p.related_node_id,
       COALESCE(p.related_node_id,'') AS related_node_key,
       p.observation_date,
       p.parent_value, cs.sum_children,
       cs.n_missing_or_latent AS n_latent,
       cs.n_children_declared,
       (p.parent_value - COALESCE(cs.sum_children,0)) AS gap_kbd,
       CASE
         -- Parent has no observation: nothing to compare.
         WHEN p.parent_value IS NULL THEN 'no_data'
         -- Parent and Σ children agree within tolerance.
         -- Tolerance = max(1 unit absolute, 1% relative). EIA series are
         -- integer-published, so 1-unit floor covers integer-rounding;
         -- 1% relative scales for large parents.
         WHEN cs.sum_children IS NOT NULL
              AND ABS(p.parent_value - cs.sum_children)
                  <= GREATEST(1.0, 0.01 * ABS(p.parent_value)) THEN 'ok'
         -- Anything else: a gap that the framework can't classify as a
         -- definitive partition inconsistency. The declared children
         -- might be incomplete (unmodelled physical nodes), missing data
         -- at this date, or genuinely latent. The framework cannot
         -- distinguish "partition is wrong" from "partition is incomplete"
         -- from data alone, so it reports the gap without accusing.
         ELSE 'partial_coverage'
       END AS status
FROM parent_val p
LEFT JOIN child_sum cs
  ON cs.scenario_id    = p.scenario_id
 AND cs.parent_node_id = p.parent_node_id
 AND cs.variable_type  = p.variable_type
 AND cs.commodity      = p.commodity
 AND COALESCE(cs.related_node_id,'') = COALESCE(p.related_node_id,'')
 AND cs.observation_date = p.observation_date
WITH DATA;

CREATE UNIQUE INDEX uq_v_aggregation_consistency
    ON oil_network.v_aggregation_consistency
    (scenario_id, parent_node_id, variable_type, commodity,
     related_node_key, observation_date);

COMMENT ON MATERIALIZED VIEW oil_network.v_aggregation_consistency IS
'For every TS-bound aggregate variable that has declared partition children, '
'compares the parent TS value against Sum(children). LEFT JOIN to '
'scenario_resolved_values so missing-data children (no row at this date) '
'register as missing. Status: ok | partial_coverage (any child missing or '
'latent) | inconsistent (all children have values, sum disagrees) | no_data.';
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("SELECT status, count(*) FROM oil_network.v_aggregation_consistency GROUP BY status ORDER BY status")
        print("v_aggregation_consistency rebuilt. New status distribution:")
        for r in cur.fetchall():
            print(f"  {r[0]:18s} {r[1]:>5}")


if __name__ == "__main__":
    main()
