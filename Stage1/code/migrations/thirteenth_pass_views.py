"""Thirteenth-pass: layered materialised views.

Architecture: four layers stacked on top of base tables. Each higher view
reads only from the layer immediately below it. The structural-data views
(L2, L3) materialise once and stay fresh until a migration touches
`variable_assignments`; the analytic views (L4) materialise per resolver
run.

Layer  View                                   Built on                      MAT?
-----  ----                                   --------                      ----
L1     v_effective_assignments                base tables                   no
L2a    v_formula_input_links                  v_effective_assignments       YES
L2b    v_aggregation_edges                    v_formula_input_links + vars  YES
L2c    v_flow_edges                           variables                     YES
L3a    v_partition_tree                       v_aggregation_edges           YES
L3b    v_node_status                          v_effective_assignments       YES
L4a    v_node_balance_check                   resolved + v_flow_edges       YES
L4b    v_aggregation_consistency              partition_tree + resolved     YES
L4c    v_aggregate_balance                    partition_tree + resolved     YES
L4d    v_inventory_changes                    resolved_values               YES

Refresh helper: `refresh_views.py`. Resolver calls it after each run;
migrations call it after touching variable_assignments.

Idempotent: every CREATE is `OR REPLACE` for regular views; for
materialised views we DROP IF EXISTS then CREATE.
"""
from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# ---------------------------------------------------------------------------
# Layer 2a — v_formula_input_links
# ---------------------------------------------------------------------------
L2A = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_formula_input_links CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_formula_input_links AS
SELECT va.scenario_id,
       va.variable_id      AS parent_variable_id,
       inp.input_var       AS child_variable_id
FROM       oil_network.v_effective_assignments va
CROSS JOIN LATERAL unnest(va.formula_inputs) AS inp(input_var)
WHERE va.formula_inputs IS NOT NULL
  AND cardinality(va.formula_inputs) > 0
WITH DATA;

CREATE UNIQUE INDEX uq_v_formula_input_links
    ON oil_network.v_formula_input_links
    (scenario_id, parent_variable_id, child_variable_id);

CREATE INDEX ix_v_formula_input_links_child
    ON oil_network.v_formula_input_links (scenario_id, child_variable_id);

COMMENT ON MATERIALIZED VIEW oil_network.v_formula_input_links IS
'Atomic (scenario, parent_variable, child_variable) — one row per arrow in '
'formula_inputs. Single source of truth for every aggregation / alias / '
'arithmetic-input relation. Higher-layer views derive from this.';
"""


# ---------------------------------------------------------------------------
# Layer 2b — v_aggregation_edges (rebuilt on L2a)
# ---------------------------------------------------------------------------
L2B = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_aggregation_edges CASCADE;
DROP VIEW IF EXISTS oil_network.v_aggregation_edges CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_aggregation_edges AS
SELECT l.scenario_id,
       vp.node_id          AS parent_node_id,
       vc.node_id          AS child_node_id,
       vp.variable_type    AS parent_variable_type,
       vc.variable_type    AS child_variable_type,
       vp.commodity        AS parent_commodity,
       vc.commodity        AS child_commodity,
       vp.related_node_id  AS parent_related_node_id,
       vc.related_node_id  AS child_related_node_id,
       l.parent_variable_id,
       l.child_variable_id,
       -- Parent's recipe under this scenario — needed for the partition filter
       va.formula                       AS parent_formula,
       (va.timeseries_id IS NOT NULL)   AS parent_is_ts_bound
FROM oil_network.v_formula_input_links l
JOIN oil_network.variables vp ON vp.variable_id = l.parent_variable_id
JOIN oil_network.variables vc ON vc.variable_id = l.child_variable_id
JOIN oil_network.v_effective_assignments va
  ON va.variable_id = l.parent_variable_id AND va.scenario_id = l.scenario_id
WHERE vp.node_id <> vc.node_id
WITH DATA;

CREATE UNIQUE INDEX uq_v_aggregation_edges
    ON oil_network.v_aggregation_edges
    (scenario_id, parent_variable_id, child_variable_id);

CREATE INDEX ix_v_aggregation_edges_parent_node
    ON oil_network.v_aggregation_edges (scenario_id, parent_node_id);
CREATE INDEX ix_v_aggregation_edges_child_node
    ON oil_network.v_aggregation_edges (scenario_id, child_node_id);

COMMENT ON MATERIALIZED VIEW oil_network.v_aggregation_edges IS
'Cross-node aggregation graph derived from v_formula_input_links. Any type, '
'any commodity, any related-node combination. The same-type-filtered subset '
'is v_partition_tree.';
"""


# ---------------------------------------------------------------------------
# Layer 2c — v_flow_edges (rebuilt as materialised; was a regular view)
# ---------------------------------------------------------------------------
L2C = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_flow_edges CASCADE;
DROP VIEW IF EXISTS oil_network.v_flow_edges CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_flow_edges AS
SELECT v.node_id           AS source,
       v.related_node_id   AS target,
       v.commodity,
       v.variable_id       AS via_variable
FROM oil_network.variables v
WHERE v.variable_type = 'outflow'
  AND v.related_node_id IS NOT NULL
WITH DATA;

CREATE UNIQUE INDEX uq_v_flow_edges
    ON oil_network.v_flow_edges (via_variable);
CREATE INDEX ix_v_flow_edges_src
    ON oil_network.v_flow_edges (source);
CREATE INDEX ix_v_flow_edges_tgt
    ON oil_network.v_flow_edges (target);

COMMENT ON MATERIALIZED VIEW oil_network.v_flow_edges IS
'Physical flow topology: outflow variables interpreted as (source -> target). '
'Recomputed when the variable graph changes; small (<500 rows).';
"""


# ---------------------------------------------------------------------------
# Layer 3a — v_partition_tree (rebuilt on L2b)
# ---------------------------------------------------------------------------
L3A = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_partition_tree CASCADE;
DROP VIEW IF EXISTS oil_network.v_partition_tree CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_partition_tree AS
SELECT scenario_id,
       parent_node_id,
       child_node_id,
       parent_variable_type   AS variable_type,
       parent_commodity       AS commodity,
       parent_related_node_id AS related_node_id,
       -- Plain column for the unique index. CONCURRENTLY refresh forbids
       -- expression-based unique indexes, so we materialise the coalesce.
       COALESCE(parent_related_node_id, '')  AS related_node_key
FROM oil_network.v_aggregation_edges
WHERE parent_variable_type = child_variable_type
  AND parent_commodity     = child_commodity
  AND COALESCE(parent_related_node_id,'') = COALESCE(child_related_node_id,'')
  -- The partition relation excludes arithmetic-residual formulas. Those
  -- formulas (parent = a - b - c) reference variables but do NOT aggregate
  -- them: their inputs are SUBTRACTED, not summed. Aliases (single bare
  -- variable_id) DO encode a partition relation since the alias source is
  -- structurally the same value carrier at the same slot.
  --
  -- Detection rule: formulas containing '-' or '+' operators are arithmetic
  -- and excluded; everything else (TS-bound, 'sum', alias) is included.
  -- Variable ids in this schema use only [a-z0-9_] so this textual check is
  -- safe.
  AND COALESCE(parent_formula, '') NOT LIKE '%-%'
  AND COALESCE(parent_formula, '') NOT LIKE '%+%'
  -- Cycle-breaker: if a reverse edge (child -> parent) also exists in
  -- v_aggregation_edges, drop OUR edge when our parent is neither TS-bound
  -- nor formula='sum'. The TS-bound / sum side wins the bidirectional
  -- cycle. This excludes the spurious (residual -> region_view) reverse
  -- edge that arises when a residual formula degenerates to a direct alias
  -- of the parent (e.g. padd1_other.production = alias(padd1_view.production)
  -- because PADD-1 has no named producers to subtract: the "arithmetic
  -- residual" collapses to a single positive term and avoids the
  -- arithmetic-operator filter above).
  AND NOT (
      parent_is_ts_bound = FALSE
      AND parent_formula <> 'sum'
      AND EXISTS (
          SELECT 1 FROM oil_network.v_aggregation_edges r
          WHERE r.scenario_id          = v_aggregation_edges.scenario_id
            AND r.parent_node_id       = v_aggregation_edges.child_node_id
            AND r.child_node_id        = v_aggregation_edges.parent_node_id
            AND r.parent_variable_type = v_aggregation_edges.parent_variable_type
            AND r.parent_commodity     = v_aggregation_edges.parent_commodity
            AND COALESCE(r.parent_related_node_id,'')
                = COALESCE(v_aggregation_edges.parent_related_node_id,'')
      )
  )
GROUP BY scenario_id, parent_node_id, child_node_id,
         parent_variable_type, parent_commodity, parent_related_node_id
WITH DATA;

CREATE UNIQUE INDEX uq_v_partition_tree
    ON oil_network.v_partition_tree
    (scenario_id, parent_node_id, child_node_id,
     variable_type, commodity, related_node_key);

CREATE INDEX ix_v_partition_tree_parent
    ON oil_network.v_partition_tree (scenario_id, parent_node_id);
CREATE INDEX ix_v_partition_tree_child
    ON oil_network.v_partition_tree (scenario_id, child_node_id);

COMMENT ON MATERIALIZED VIEW oil_network.v_partition_tree IS
'Same-type, same-commodity, same-related-node subset of v_aggregation_edges. '
'The partition spine: parent_node aggregates child_node for the named '
'variable_type. Used by audit_partition_gaps and every consumer that needs '
'the partition hierarchy.';
"""


# ---------------------------------------------------------------------------
# Layer 3b — v_node_status (recreated as MATERIALIZED, was regular in 12th)
# ---------------------------------------------------------------------------
L3B = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_node_status CASCADE;
DROP VIEW IF EXISTS oil_network.v_node_status CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_node_status AS
SELECT va.scenario_id,
       v.node_id,
       COUNT(*) FILTER (WHERE va.timeseries_id IS NOT NULL)            AS n_ts,
       COUNT(*) FILTER (WHERE va.formula = '0')                        AS n_zero,
       COUNT(*) FILTER (WHERE va.formula = 'latent()')                 AS n_latent,
       COUNT(*) FILTER (WHERE va.timeseries_id IS NULL
                          AND va.formula IS NOT NULL
                          AND va.formula NOT IN ('','0','latent()'))   AS n_derived,
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
GROUP BY va.scenario_id, v.node_id
WITH DATA;

CREATE UNIQUE INDEX uq_v_node_status
    ON oil_network.v_node_status (scenario_id, node_id);

CREATE INDEX ix_v_node_status_status
    ON oil_network.v_node_status (scenario_id, status);

COMMENT ON MATERIALIZED VIEW oil_network.v_node_status IS
'Inferred node status per (scenario, node): authoritative / derived / collapsed. '
'Derived from v_effective_assignments. Supersedes the assets.starter_status / '
'nodes.starter_status columns.';
"""


# ---------------------------------------------------------------------------
# Layer 4a — v_node_balance_check (preserved; depends on resolved values)
# ---------------------------------------------------------------------------
L4A = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_node_balance_check CASCADE;
DROP VIEW IF EXISTS oil_network.v_node_balance_check CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_node_balance_check AS
WITH per_var AS (
    SELECT srv.scenario_id, srv.observation_date,
           v.node_id, v.variable_type, srv.value, srv.source
    FROM oil_network.scenario_resolved_values srv
    JOIN oil_network.variables v USING (variable_id)
), agg AS (
    SELECT scenario_id, observation_date, node_id,
           SUM(value) FILTER (WHERE variable_type = 'inflow'  AND value IS NOT NULL) AS sum_in,
           SUM(value) FILTER (WHERE variable_type = 'outflow' AND value IS NOT NULL) AS sum_out_obs,
           COUNT(*)   FILTER (WHERE variable_type = 'outflow' AND source = 'latent') AS n_outflows_latent,
           MAX(value) FILTER (WHERE variable_type = 'production') AS p,
           MAX(value) FILTER (WHERE variable_type = 'consumption') AS c,
           MAX(value) FILTER (WHERE variable_type = 'balancing_item') AS b
    FROM per_var
    GROUP BY scenario_id, observation_date, node_id
)
SELECT scenario_id, observation_date, node_id,
       sum_in, sum_out_obs,
       n_outflows_latent,
       (COALESCE(sum_in,0) + COALESCE(p,0) - COALESCE(c,0) + COALESCE(b,0)) AS sum_out_implied,
       (COALESCE(sum_out_obs,0)
            - (COALESCE(sum_in,0) + COALESCE(p,0) - COALESCE(c,0) + COALESCE(b,0))) AS gap_kbd,
       CASE
         WHEN sum_in IS NULL AND sum_out_obs IS NULL THEN 'no_flows'
         WHEN sum_in IS NOT NULL AND sum_out_obs IS NULL AND n_outflows_latent > 0 THEN 'in_only_implied'
         WHEN sum_in IS NULL AND sum_out_obs IS NOT NULL THEN 'out_only'
         WHEN ABS(COALESCE(sum_out_obs,0)
                    - (COALESCE(sum_in,0) + COALESCE(p,0) - COALESCE(c,0) + COALESCE(b,0))) < 1 THEN 'closes'
         ELSE 'open'
       END AS status
FROM agg
WITH DATA;

CREATE UNIQUE INDEX uq_v_node_balance_check
    ON oil_network.v_node_balance_check (scenario_id, observation_date, node_id);

COMMENT ON MATERIALIZED VIEW oil_network.v_node_balance_check IS
'Per-node mass-balance closure: sum_in + P - C + B = sum_out_implied. '
'gap_kbd compares observed sum_out against implied. Refreshed after each '
'resolver run.';
"""


# ---------------------------------------------------------------------------
# Layer 4b — v_aggregation_consistency (rebuilt on partition tree)
# ---------------------------------------------------------------------------
L4B = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_aggregation_consistency CASCADE;
DROP VIEW IF EXISTS oil_network.v_aggregation_consistency CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_aggregation_consistency AS
WITH parent_var AS (
    -- Variables that are TS-bound (authoritative) AND have aggregation children:
    -- both sides of the identity are independently known.
    SELECT DISTINCT pt.scenario_id, pt.parent_node_id, pt.variable_type,
                    pt.commodity, pt.related_node_id, v.variable_id AS parent_var
    FROM oil_network.v_partition_tree pt
    JOIN oil_network.variables v
      ON v.node_id = pt.parent_node_id
     AND v.variable_type = pt.variable_type
     AND v.commodity = pt.commodity
     AND COALESCE(v.related_node_id,'') = COALESCE(pt.related_node_id,'')
    JOIN oil_network.v_effective_assignments va
      ON va.variable_id = v.variable_id
     AND va.scenario_id = pt.scenario_id
    WHERE va.timeseries_id IS NOT NULL
), parent_val AS (
    SELECT p.scenario_id, p.parent_node_id, p.variable_type, p.commodity, p.related_node_id,
           srv.observation_date, srv.value AS parent_value
    FROM parent_var p
    JOIN oil_network.scenario_resolved_values srv
      ON srv.scenario_id = p.scenario_id
     AND srv.variable_id = p.parent_var
), child_sum AS (
    SELECT pt.scenario_id, pt.parent_node_id, pt.variable_type, pt.commodity, pt.related_node_id,
           srv.observation_date,
           SUM(srv.value) FILTER (WHERE srv.value IS NOT NULL) AS sum_children,
           COUNT(*)       FILTER (WHERE srv.value IS NULL OR srv.source = 'latent') AS n_latent
    FROM oil_network.v_partition_tree pt
    JOIN oil_network.variables vc
      ON vc.node_id = pt.child_node_id
     AND vc.variable_type = pt.variable_type
     AND vc.commodity = pt.commodity
     AND COALESCE(vc.related_node_id,'') = COALESCE(pt.related_node_id,'')
    JOIN oil_network.scenario_resolved_values srv
      ON srv.scenario_id = pt.scenario_id
     AND srv.variable_id = vc.variable_id
    GROUP BY pt.scenario_id, pt.parent_node_id, pt.variable_type, pt.commodity,
             pt.related_node_id, srv.observation_date
)
SELECT p.scenario_id, p.parent_node_id, p.variable_type, p.commodity, p.related_node_id,
       COALESCE(p.related_node_id,'') AS related_node_key,
       p.observation_date,
       p.parent_value, cs.sum_children, cs.n_latent,
       (p.parent_value - COALESCE(cs.sum_children,0)) AS gap_kbd,
       CASE
         WHEN cs.n_latent > 0 THEN 'partial_coverage'
         WHEN p.parent_value IS NULL OR cs.sum_children IS NULL THEN 'no_data'
         WHEN ABS(p.parent_value - cs.sum_children) < 1 THEN 'ok'
         ELSE 'inconsistent'
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
'compares the parent TS value against Sum(children). Flags ok / partial_coverage '
'(some children latent) / inconsistent / no_data. Rebuilt on top of '
'v_partition_tree (thirteenth pass).';
"""


# ---------------------------------------------------------------------------
# Layer 4c — v_inventory_changes (was unused inline; keep as MAT view)
# ---------------------------------------------------------------------------
L4C = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_inventory_changes CASCADE;
DROP VIEW IF EXISTS oil_network.v_inventory_changes CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_inventory_changes AS
WITH ordered AS (
    SELECT srv.scenario_id, srv.observation_date, srv.variable_id,
           v.node_id, srv.value AS s_mbbl,
           LAG(srv.value) OVER (PARTITION BY srv.scenario_id, srv.variable_id
                                ORDER BY srv.observation_date) AS s_prev_mbbl
    FROM oil_network.scenario_resolved_values srv
    JOIN oil_network.variables v USING (variable_id)
    WHERE v.variable_type = 'inventory'
      AND srv.value IS NOT NULL
)
SELECT scenario_id, observation_date, variable_id, node_id,
       s_mbbl,
       s_prev_mbbl,
       (s_mbbl - s_prev_mbbl) AS delta_mbbl,
       CASE
         WHEN s_prev_mbbl IS NULL THEN NULL
         ELSE (s_mbbl - s_prev_mbbl)
              / EXTRACT(DAY FROM (observation_date + INTERVAL '1 month' - INTERVAL '1 day'
                                    - (DATE_TRUNC('month', observation_date))))
       END AS delta_kbd
FROM ordered
WITH DATA;

CREATE UNIQUE INDEX uq_v_inventory_changes
    ON oil_network.v_inventory_changes (scenario_id, variable_id, observation_date);

COMMENT ON MATERIALIZED VIEW oil_network.v_inventory_changes IS
'Per-stock-series delta in MBBL plus the same delta as kbd over the month. '
'Refreshed after each resolver run.';
"""


# ---------------------------------------------------------------------------
# Layer 4d — v_aggregate_balance (closed mass balance for usa + 5 PADDs)
# ---------------------------------------------------------------------------
# (We rebuild this from partition_tree + resolved values. It's similar to
# v_node_balance_check but specialised to the partition-spine nodes.)
L4D = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_aggregate_balance CASCADE;
DROP VIEW IF EXISTS oil_network.v_aggregate_balance CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_aggregate_balance AS
SELECT bc.scenario_id, bc.observation_date, bc.node_id,
       bc.sum_in, bc.sum_out_obs, bc.sum_out_implied,
       bc.gap_kbd, bc.status
FROM oil_network.v_node_balance_check bc
WHERE bc.node_id IN ('usa_view','padd1_view','padd2_view','padd3_view','padd4_view','padd5_view')
WITH DATA;

CREATE UNIQUE INDEX uq_v_aggregate_balance
    ON oil_network.v_aggregate_balance (scenario_id, observation_date, node_id);

COMMENT ON MATERIALIZED VIEW oil_network.v_aggregate_balance IS
'Closed mass balance for usa_view + the 5 PADDs. Subset of v_node_balance_check '
'filtered to the geographic partition spine.';
"""


# ---------------------------------------------------------------------------
# Layer 4e — v_node_pcisob (per-node per-date balance aggregates)
# ---------------------------------------------------------------------------
# Pre-computes the balance equation's per-letter aggregates so renderers
# don't have to iterate variables_on_node × dates at render time. One row
# per (scenario, node, date) carries the full P/C/I/O/B/S/dS picture used
# by the balance HTML. Refreshed by the resolver alongside other L4 views.
L4E = """
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_node_pcisob CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_node_pcisob AS
WITH agg AS (
    SELECT srv.scenario_id, v.node_id, srv.observation_date,
        MAX(srv.value) FILTER (WHERE v.variable_type='production'     AND srv.source <> 'zero') AS p,
        MAX(srv.value) FILTER (WHERE v.variable_type='consumption'    AND srv.source <> 'zero') AS c,
        SUM(srv.value) FILTER (WHERE v.variable_type='inflow'         AND srv.source <> 'zero') AS i,
        SUM(srv.value) FILTER (WHERE v.variable_type='outflow'        AND srv.source <> 'zero') AS o,
        MAX(srv.value) FILTER (WHERE v.variable_type='balancing_item' AND srv.source <> 'zero') AS b,
        MAX(srv.value) FILTER (WHERE v.variable_type='inventory'      AND srv.source <> 'zero') AS s_mbbl
    FROM oil_network.scenario_resolved_values srv
    JOIN oil_network.variables v USING (variable_id)
    WHERE srv.value IS NOT NULL
    GROUP BY srv.scenario_id, v.node_id, srv.observation_date
)
SELECT scenario_id, node_id, observation_date,
       p, c, i, o, b, s_mbbl,
       (s_mbbl - LAG(s_mbbl) OVER (PARTITION BY scenario_id, node_id ORDER BY observation_date))
         / EXTRACT(DAY FROM (date_trunc('month', observation_date)
                              + interval '1 month - 1 day'))::numeric
         AS ds_kbd
FROM agg
WITH DATA;

CREATE UNIQUE INDEX uq_v_node_pcisob
    ON oil_network.v_node_pcisob (scenario_id, node_id, observation_date);
CREATE INDEX ix_v_node_pcisob_node
    ON oil_network.v_node_pcisob (scenario_id, node_id);

COMMENT ON MATERIALIZED VIEW oil_network.v_node_pcisob IS
'Per-node per-date balance aggregates: P, C, I (sum of inflows), O (sum of '
'outflows), B, S (inventory level in mbbl), dS (inventory delta in kbd). '
'Pre-computed from scenario_resolved_values + variables so the balance HTML '
'renderer can lookup the full row per (node, date) without iterating '
'variable-by-variable. Refreshed by the resolver as part of the L4 analytic '
'mat-view refresh.';
"""


# ---------------------------------------------------------------------------
# Retire superseded views
# ---------------------------------------------------------------------------
RETIRE = """
DROP VIEW IF EXISTS oil_network.v_scope_authoritative_nodes CASCADE;
DROP VIEW IF EXISTS oil_network.v_scope_collapsed_nodes    CASCADE;
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Legacy view-kind conflict: build_oil_network.ipynb creates several of these
# names as REGULAR views. `DROP MATERIALIZED VIEW IF EXISTS name` errors with
# WrongObjectType when `name` exists as a regular view (PG checks name before
# kind). Pre-drop the regular-view forms unconditionally so the matview CREATEs
# below have a clean slate. CASCADE picks up any dependent views (e.g.
# v_scope_* depending on v_aggregation_edges).
PREDROP = """
DO $$
DECLARE
    obj record;
    target_names text[] := ARRAY[
        'v_formula_input_links',
        'v_aggregation_edges',
        'v_flow_edges',
        'v_partition_tree',
        'v_node_status',
        'v_node_balance_check',
        'v_aggregation_consistency',
        'v_inventory_changes',
        'v_node_pcisob',
        'v_aggregate_balance'
    ];
BEGIN
    FOR obj IN
        SELECT c.relname, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON c.relnamespace = n.oid
        WHERE n.nspname = 'oil_network'
          AND c.relname = ANY(target_names)
    LOOP
        IF obj.relkind = 'm' THEN
            EXECUTE format('DROP MATERIALIZED VIEW IF EXISTS oil_network.%I CASCADE', obj.relname);
        ELSIF obj.relkind = 'v' THEN
            EXECUTE format('DROP VIEW IF EXISTS oil_network.%I CASCADE', obj.relname);
        END IF;
    END LOOP;
END$$;
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("== pre-drop legacy regular-view forms ==")
        cur.execute(PREDROP)
        conn.commit()

        for label, sql in [
            ("L2a v_formula_input_links",        L2A),
            ("L2b v_aggregation_edges",          L2B),
            ("L2c v_flow_edges (re-materialised)", L2C),
            ("L3a v_partition_tree",             L3A),
            ("L3b v_node_status",                L3B),
            ("L4a v_node_balance_check",         L4A),
            ("L4b v_aggregation_consistency",    L4B),
            ("L4c v_inventory_changes",          L4C),
            ("L4e v_node_pcisob",                L4E),
            ("L4d v_aggregate_balance",          L4D),
            ("Retire scope_authoritative / scope_collapsed", RETIRE),
        ]:
            print(f"== {label} ==")
            cur.execute(sql)
            conn.commit()
            # row count for the freshly materialised view
            name = label.split()[1] if label.startswith("L") else None
            if name and name.startswith("v_"):
                cur.execute(f"SELECT COUNT(*) FROM oil_network.{name}")
                print(f"   rows: {cur.fetchone()[0]:,}")
        print("\nAll views built. Use refresh_views.py to refresh.")


if __name__ == "__main__":
    main()
