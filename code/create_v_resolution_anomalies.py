"""Create v_resolution_anomalies — general post-build data-quality audit.

Surfaces suspicious patterns in scenario_resolved_values that would otherwise
require ad-hoc queries to discover:

    negative_historical  — physically impossible values (P, C, S, inflow,
                            outflow < 0) at historical dates. Real bugs.
    negative_forecast    — same, at forecast-horizon dates (> CURRENT_DATE).
                            Typically LOCF + heterogeneous-source-horizon
                            artefacts (see Corollary D-bis in
                            claude/DESIGN_PRINCIPLES.md).
    long_locf_run        — TS-bound rows where formula_used = 'locf(D0)' and
                            the run length (observation_date − D0) exceeds 90
                            days. Long carry-forwards relative to expected
                            monthly EIA cadence — flag-worthy data quality.
    unresolved           — scenario_resolved_values rows with source='unresolved'.
                            Should be 0 in a healthy run; any row here
                            indicates a broken assignment.

The view is read-only; downstream consumers (audit script, future LP exporter,
balance UI cell coloring) query it directly.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
DROP VIEW IF EXISTS oil_network.v_resolution_anomalies CASCADE;

CREATE VIEW oil_network.v_resolution_anomalies AS
-- Negative P/C/I/inflow/outflow — physically impossible.
-- Severity rule: 'observed' negative = real bug (EIA published negative);
-- 'derived' negative = LOCF / arithmetic-residual cascade through divergent
-- source horizons (Corollary D-bis). Reported either way; severity differs.
SELECT srv.scenario_id,
       CASE WHEN srv.source = 'observed' THEN 'negative_observed'
            ELSE 'negative_derived' END AS anomaly_kind,
       CASE WHEN srv.source = 'observed' THEN 'high'
            ELSE 'low' END AS severity,
       v.variable_id, v.node_id, v.variable_type, v.related_node_id,
       srv.observation_date, srv.value, srv.source, srv.formula_used,
       NULL::INTEGER AS locf_run_days
  FROM oil_network.scenario_resolved_values srv
  JOIN oil_network.variables v ON v.variable_id = srv.variable_id
 WHERE srv.value < 0
   AND srv.source IN ('observed','derived')
   AND v.variable_type IN ('production','consumption','inventory','inflow','outflow')

UNION ALL

-- Long LOCF runs (TS gap > ~3 months relative to expected monthly cadence)
SELECT srv.scenario_id,
       'long_locf_run' AS anomaly_kind,
       'low' AS severity,
       v.variable_id, v.node_id, v.variable_type, v.related_node_id,
       srv.observation_date, srv.value, srv.source, srv.formula_used,
       (srv.observation_date - SUBSTRING(srv.formula_used FROM 'locf\\(([0-9-]+)\\)')::DATE)::INTEGER AS locf_run_days
  FROM oil_network.scenario_resolved_values srv
  JOIN oil_network.variables v ON v.variable_id = srv.variable_id
 WHERE srv.formula_used LIKE 'locf(%'
   AND (srv.observation_date - SUBSTRING(srv.formula_used FROM 'locf\\(([0-9-]+)\\)')::DATE) > 90

UNION ALL

-- Unresolved variables — broken assignments
SELECT srv.scenario_id,
       'unresolved' AS anomaly_kind,
       'high' AS severity,
       v.variable_id, v.node_id, v.variable_type, v.related_node_id,
       srv.observation_date, srv.value, srv.source, srv.formula_used,
       NULL::INTEGER AS locf_run_days
  FROM oil_network.scenario_resolved_values srv
  JOIN oil_network.variables v ON v.variable_id = srv.variable_id
 WHERE srv.source = 'unresolved'
;
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        # Sanity-count rows so the orchestrator log shows the view is alive.
        cur.execute("SELECT anomaly_kind, severity, COUNT(*) FROM oil_network.v_resolution_anomalies "
                    "GROUP BY anomaly_kind, severity ORDER BY severity, anomaly_kind")
        rows = cur.fetchall()
        if rows:
            print(f"  v_resolution_anomalies created. Current row counts:")
            for kind, sev, n in rows:
                print(f"    [{sev}]  {kind:<22} {n:>6}")
        else:
            print(f"  v_resolution_anomalies created. No anomalies detected.")
        conn.commit()


if __name__ == "__main__":
    main()
