"""Create oil_network.v_node_balance_check — surfaces the mass-balance closure at every node.

For each (scenario, node, date), computes from scenario_resolved_values:

  sum_in           = Σ resolved inflow values    at the node
  sum_out_obs      = Σ resolved outflow values   at the node
  P, C, B          = resolved production / consumption / balancing_item value at the node
  delta_S          = inventory(d) − inventory(d-1), in kbd  (or NULL if not computable)
  sum_out_implied  = sum_in + P − C − delta_S + B          (mass-balance solve for ΣO)
  gap              = sum_out_obs − sum_out_implied         (closure check)

Plus diagnostic columns:
  n_in_resolved, n_in_latent  — inflow edges with / without a resolved value
  n_out_resolved, n_out_latent
  status                       — 'closed' (gap≈0), 'in-only' (ΣO unknown, ΣI implies it),
                                 'out-only', 'underdetermined', 'open'

This is the UI artefact for "permian_tx_gathering: ΣO = 4313 kbd implied by the
inflow + structural-zero defaults, even though the 3 individual splits are
latent." Pure pass-throughs (default P=C=ΔS=B=0) will show sum_out_implied = sum_in.

Idempotent: CREATE OR REPLACE.
"""
from __future__ import annotations

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
CREATE OR REPLACE VIEW oil_network.v_node_balance_check AS
WITH per_node AS (
    SELECT
        srv.scenario_id,
        v.node_id,
        srv.observation_date,
        v.variable_type,
        srv.value,
        srv.source,
        srv.variable_id
    FROM oil_network.scenario_resolved_values srv
    JOIN oil_network.variables v ON v.variable_id = srv.variable_id
),
in_out_tally AS (
    SELECT
        scenario_id,
        node_id,
        observation_date,
        SUM(CASE WHEN variable_type = 'inflow'  AND value IS NOT NULL THEN value END) AS sum_in,
        COUNT(CASE WHEN variable_type = 'inflow'  AND value IS NOT NULL THEN 1 END)   AS n_in_resolved,
        COUNT(CASE WHEN variable_type = 'inflow'  AND source = 'latent' THEN 1 END)   AS n_in_latent,
        SUM(CASE WHEN variable_type = 'outflow' AND value IS NOT NULL THEN value END) AS sum_out_obs,
        COUNT(CASE WHEN variable_type = 'outflow' AND value IS NOT NULL THEN 1 END)   AS n_out_resolved,
        COUNT(CASE WHEN variable_type = 'outflow' AND source = 'latent' THEN 1 END)   AS n_out_latent
    FROM per_node
    GROUP BY scenario_id, node_id, observation_date
),
scalar_terms AS (
    -- One row per (scenario, node, date) with the four non-relational values.
    SELECT
        scenario_id,
        node_id,
        observation_date,
        MAX(CASE WHEN variable_type = 'production'     THEN value END) AS p_val,
        MAX(CASE WHEN variable_type = 'consumption'    THEN value END) AS c_val,
        MAX(CASE WHEN variable_type = 'inventory'      THEN value END) AS inv_val,
        MAX(CASE WHEN variable_type = 'balancing_item' THEN value END) AS b_val
    FROM per_node
    GROUP BY scenario_id, node_id, observation_date
),
inv_delta AS (
    -- ΔS in kbd: (S_now - S_prev) / days_in_month. Window LAG over months at same node.
    SELECT
        scenario_id,
        node_id,
        observation_date,
        inv_val,
        LAG(inv_val) OVER (PARTITION BY scenario_id, node_id ORDER BY observation_date) AS inv_prev,
        EXTRACT(DAY FROM (
            date_trunc('month', observation_date) + interval '1 month - 1 day'
        ))::int AS days_in_month
    FROM scalar_terms
)
SELECT
    iot.scenario_id,
    iot.node_id,
    iot.observation_date,
    iot.sum_in,
    iot.sum_out_obs,
    iot.n_in_resolved,
    iot.n_in_latent,
    iot.n_out_resolved,
    iot.n_out_latent,
    s.p_val   AS production,
    s.c_val   AS consumption,
    s.inv_val AS inventory,
    s.b_val   AS balancing_item,
    CASE
        WHEN s.inv_val IS NULL OR id.inv_prev IS NULL THEN NULL
        ELSE (s.inv_val - id.inv_prev)::float / id.days_in_month
    END AS delta_s_kbd,
    -- ΣO_implied = ΣI + P − C − ΔS + B
    CASE
        WHEN iot.sum_in IS NULL THEN NULL
        WHEN s.p_val IS NULL OR s.c_val IS NULL OR s.b_val IS NULL THEN NULL
        WHEN s.inv_val IS NOT NULL AND id.inv_prev IS NULL THEN NULL
        ELSE iot.sum_in
             + s.p_val
             - s.c_val
             - COALESCE((s.inv_val - id.inv_prev)::float / id.days_in_month, 0)
             + s.b_val
    END AS sum_out_implied,
    -- gap = observed_ΣO − implied_ΣO
    CASE
        WHEN iot.sum_out_obs IS NOT NULL AND iot.sum_in IS NOT NULL
         AND s.p_val IS NOT NULL AND s.c_val IS NOT NULL AND s.b_val IS NOT NULL
         AND (s.inv_val IS NULL OR id.inv_prev IS NOT NULL)
        THEN iot.sum_out_obs - (
            iot.sum_in + s.p_val - s.c_val
            - COALESCE((s.inv_val - id.inv_prev)::float / id.days_in_month, 0)
            + s.b_val
        )
    END AS gap_kbd,
    -- status classification
    CASE
        WHEN iot.sum_in IS NULL AND iot.sum_out_obs IS NULL                THEN 'underdetermined'
        WHEN iot.sum_in IS NOT NULL AND iot.sum_out_obs IS NOT NULL
         AND ABS(COALESCE(
             iot.sum_out_obs - (iot.sum_in + s.p_val - s.c_val
                 - COALESCE((s.inv_val - id.inv_prev)::float / id.days_in_month, 0)
                 + s.b_val), 9999)) < 1.0
                                                                          THEN 'closed'
        WHEN iot.sum_in IS NOT NULL AND iot.sum_out_obs IS NULL
         AND s.p_val IS NOT NULL AND s.c_val IS NOT NULL AND s.b_val IS NOT NULL
                                                                          THEN 'in-only (ΣO implied)'
        WHEN iot.sum_in IS NULL AND iot.sum_out_obs IS NOT NULL            THEN 'out-only'
        ELSE                                                                    'open'
    END AS status
FROM in_out_tally iot
LEFT JOIN scalar_terms s
       ON s.scenario_id      = iot.scenario_id
      AND s.node_id          = iot.node_id
      AND s.observation_date = iot.observation_date
LEFT JOIN inv_delta id
       ON id.scenario_id      = iot.scenario_id
      AND id.node_id          = iot.node_id
      AND id.observation_date = iot.observation_date;

COMMENT ON VIEW oil_network.v_node_balance_check IS
'Per-node mass-balance closure check. sum_out_implied = ΣI + P − C − ΔS + B is the value ΣO MUST take to satisfy the node-level balance; gap_kbd = observed − implied flags discrepancies. At pure pass-throughs (gathering, origin_terminal, pipeline, import/export_terminal) with structural-zero defaults, sum_out_implied equals sum_in — even when individual outflow splits are latent.';
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
        # Demo: pull the 12 asymmetric junctions from the audit
        cur.execute("""
            SELECT node_id, sum_in, sum_out_obs, sum_out_implied, gap_kbd, status,
                   n_out_latent
            FROM oil_network.v_node_balance_check
            WHERE scenario_id = 'starter_us_crude_2015_2025'
              AND observation_date = '2024-12-01'
              AND node_id IN (
                'permian_tx_gathering','bakken_nd_gathering','eagle_ford_gathering',
                'permian_nm_gathering','ans_gathering',
                'guernsey_hub','houston_hub','patoka_hub',
                'padd1_imports_agg','padd3_imports_agg','padd5_imports_agg',
                'pipe_express_platte','pipe_trans_mountain_tmx',
                'midland_origin','wink_origin','crane_origin'
              )
            ORDER BY node_id
        """)
        rows = cur.fetchall()

    print("Sample at 2024-12-01 — pass-through junctions, observed ΣI vs implied ΣO:\n")
    print(f"  {'node':<28}  {'ΣI':>9}  {'ΣO_obs':>9}  {'ΣO_implied':>11}  {'gap':>9}  {'n_lat_O':>7}  status")
    for r in rows:
        node, si, so, simp, gap, st, nlat = r
        f = lambda v: f"{v:>9.1f}" if v is not None else "       —"
        print(f"  {node:<28}  {f(si)}  {f(so)}  {f(simp)}  {f(gap)}  {nlat:>7}  {st}")


if __name__ == "__main__":
    main()
