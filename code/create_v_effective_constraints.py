"""Create `oil_network.v_effective_constraints` — the join that downstream reads.

For every variable in a scenario, compute the active min/max bound by
joining:

  1. `variable_constraints` (scenario-specific overlay; takes precedence)
  2. `asset_capacities`     (physical, scenario-agnostic; fallback)

Mapping from variable to asset capacity:

  variable_type        capacity_kind
  -------------        -------------
  consumption          consumption     (refineries)
  production           production      (basins, foreign production aggs)
  inventory            storage         (terminals, tanks, SPR sites)
  outflow              throughput      (pipelines)

The view emits one row per (scenario, variable, kind) — the effective
bound applicable today. Time-aware querying (effective_from semantics)
is left to the audit / LP exporter, which adds a `WHERE effective_from
<= :as_of` clause and uses DISTINCT ON.

NULL min_value or max_value means unbounded in that direction.

The view is a regular view (not materialised) — small result set, cheap
to recompute. If volume becomes an issue, promote to matview and refresh
alongside the L4 analytic views.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
CREATE OR REPLACE VIEW oil_network.v_effective_constraints AS

-- Variables that map cleanly onto a single asset capacity slot.
WITH variable_kind AS (
    SELECT v.variable_id, v.node_id AS asset_id, v.commodity,
           CASE v.variable_type
               WHEN 'consumption' THEN 'consumption'
               WHEN 'production'  THEN 'production'
               WHEN 'inventory'   THEN 'storage'
               WHEN 'outflow'     THEN 'throughput'
               -- inflow / balancing_item: not directly bound by an asset capacity.
               -- (An inflow can be bound indirectly via its mirror outflow; the
               -- LP exporter can handle that. Skip here for now.)
               ELSE NULL
           END AS capacity_kind
    FROM oil_network.variables v
),
-- Per-scenario overlay rows (scenario-specific)
overlay AS (
    SELECT vk.variable_id, vc.scenario_id, vc.kind AS capacity_kind,
           vc.effective_from, vc.min_value, vc.max_value, vc.unit, vc.source
    FROM variable_kind vk
    JOIN oil_network.variable_constraints vc ON vc.variable_id = vk.variable_id
),
-- Physical fallback rows (asset-level, available across all scenarios)
fallback AS (
    SELECT vk.variable_id, s.scenario_id, ac.capacity_kind,
           ac.effective_from, ac.min_value, ac.max_value, ac.unit, ac.source
    FROM variable_kind vk
    CROSS JOIN oil_network.scenarios s
    JOIN oil_network.asset_capacities ac
      ON ac.asset_id = vk.asset_id
     AND ac.commodity = vk.commodity
     AND ac.capacity_kind = vk.capacity_kind
)
-- Union of overlay and fallback; overlay always wins via the priority col
SELECT scenario_id, variable_id, capacity_kind, effective_from,
       min_value, max_value, unit, source, layer
FROM (
    SELECT scenario_id, variable_id, capacity_kind, effective_from,
           min_value, max_value, unit, source,
           'variable_constraint' AS layer,
           1 AS priority
    FROM overlay
    UNION ALL
    SELECT scenario_id, variable_id, capacity_kind, effective_from,
           min_value, max_value, unit, source,
           'asset_capacity' AS layer,
           2 AS priority
    FROM fallback
    WHERE NOT EXISTS (
        SELECT 1 FROM overlay o
        WHERE o.variable_id    = fallback.variable_id
          AND o.scenario_id    = fallback.scenario_id
          AND o.capacity_kind  = fallback.capacity_kind
          AND o.effective_from = fallback.effective_from
    )
) u;

COMMENT ON VIEW oil_network.v_effective_constraints IS
'Per-(scenario, variable, capacity_kind, effective_from) effective bound. '
'Joins variable_constraints (overlay, takes precedence) over asset_capacities '
'(physical fallback). The `layer` column tags which source produced each row '
'so the audit can attribute violations correctly.';
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("SELECT count(*) FROM oil_network.v_effective_constraints")
        n = cur.fetchone()[0]
    print(f"v_effective_constraints created. {n} (scenario, variable, kind, effective_from) rows visible.")


if __name__ == "__main__":
    main()
