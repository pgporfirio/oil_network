"""Inspection views over the `refineries.*` schema.

Idempotent — safe to re-run. These views are read-only and give a fast
overview of what each agent run produced.

Views created:

    refineries.v_refinery_overview         one row per refinery, counts + last run
    refineries.v_refinery_units            joined process units with source URLs
    refineries.v_refinery_slate            joined slate observations with sources
    refineries.v_refinery_events           joined events with sources
    refineries.v_refinery_financials       joined financial periods with sources
    refineries.v_refinery_runs_monthly     joined monthly throughput series
    refineries.v_refinery_runs_summary     per-refinery, per-metric series stats
    refineries.v_refinery_sources          joined sources, latest first
    refineries.v_exploration_runs          run log, latest first

Run standalone:

    .venv\\Scripts\\python.exe Stage2\\code\\migrations\\create_refineries_views.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
CREATE OR REPLACE VIEW refineries.v_refinery_overview AS
SELECT
    r.refinery_id,
    r.name,
    r.corporation,
    r.operator,
    r.state,
    r.padd,
    r.rdist_label,
    r.duoarea_code,
    r.capacity_bpd,
    r.last_explored_at,
    (SELECT COUNT(*) FROM refineries.process_units pu WHERE pu.refinery_id = r.refinery_id) AS n_units,
    (SELECT COUNT(*) FROM refineries.slate          sl WHERE sl.refinery_id = r.refinery_id) AS n_slate,
    (SELECT COUNT(*) FROM refineries.events         ev WHERE ev.refinery_id = r.refinery_id) AS n_events,
    (SELECT COUNT(*) FROM refineries.financials     fi WHERE fi.refinery_id = r.refinery_id) AS n_financials,
    (SELECT COUNT(*) FROM refineries.runs_monthly   rm WHERE rm.refinery_id = r.refinery_id) AS n_monthly_rows,
    (SELECT COUNT(DISTINCT rm.metric)
        FROM refineries.runs_monthly rm WHERE rm.refinery_id = r.refinery_id) AS n_metrics,
    (SELECT MIN(rm.month) FROM refineries.runs_monthly rm WHERE rm.refinery_id = r.refinery_id) AS monthly_from,
    (SELECT MAX(rm.month) FROM refineries.runs_monthly rm WHERE rm.refinery_id = r.refinery_id) AS monthly_to,
    (SELECT COUNT(*) FROM refineries.sources  s  WHERE s.refinery_id = r.refinery_id) AS n_sources,
    (SELECT COUNT(*) FROM refineries.exploration_runs er
       WHERE er.refinery_id = r.refinery_id AND er.status = 'success') AS n_successful_runs,
    (SELECT er.cost_usd FROM refineries.exploration_runs er
       WHERE er.refinery_id = r.refinery_id ORDER BY er.started_at DESC LIMIT 1) AS last_cost_usd,
    (SELECT er.tool_calls FROM refineries.exploration_runs er
       WHERE er.refinery_id = r.refinery_id ORDER BY er.started_at DESC LIMIT 1) AS last_tool_calls,
    (SELECT er.summary FROM refineries.exploration_runs er
       WHERE er.refinery_id = r.refinery_id ORDER BY er.started_at DESC LIMIT 1) AS last_summary
FROM refineries.refinery r;

CREATE OR REPLACE VIEW refineries.v_refinery_units AS
SELECT
    pu.refinery_id,
    r.name           AS refinery_name,
    pu.unit_type,
    pu.capacity_bpd,
    pu.status,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM refineries.process_units pu
JOIN refineries.refinery r USING (refinery_id)
LEFT JOIN refineries.sources s ON s.source_id = pu.source_id
ORDER BY pu.refinery_id, pu.unit_type;

CREATE OR REPLACE VIEW refineries.v_refinery_slate AS
SELECT
    sl.refinery_id,
    r.name           AS refinery_name,
    sl.period_start,
    sl.period_end,
    sl.grade_name,
    sl.api_gravity,
    sl.sulphur_pct,
    sl.share_pct,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM refineries.slate sl
JOIN refineries.refinery r USING (refinery_id)
LEFT JOIN refineries.sources s ON s.source_id = sl.source_id
ORDER BY sl.refinery_id, sl.period_start DESC, sl.grade_name;

CREATE OR REPLACE VIEW refineries.v_refinery_events AS
SELECT
    ev.event_id,
    ev.refinery_id,
    r.name           AS refinery_name,
    ev.event_type,
    ev.start_date,
    ev.end_date,
    ev.units_affected,
    ev.capacity_impact_bpd,
    ev.description,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM refineries.events ev
JOIN refineries.refinery r USING (refinery_id)
LEFT JOIN refineries.sources s ON s.source_id = ev.source_id
ORDER BY ev.refinery_id, ev.start_date DESC NULLS LAST, ev.event_id;

CREATE OR REPLACE VIEW refineries.v_refinery_financials AS
SELECT
    fi.refinery_id,
    r.name           AS refinery_name,
    fi.period_start,
    fi.period_end,
    fi.period_type,
    fi.throughput_bpd,
    fi.utilisation_pct,
    fi.revenue_usd_m,
    fi.ebitda_usd_m,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM refineries.financials fi
JOIN refineries.refinery r USING (refinery_id)
LEFT JOIN refineries.sources s ON s.source_id = fi.source_id
ORDER BY fi.refinery_id, fi.period_start DESC, fi.period_type;

CREATE OR REPLACE VIEW refineries.v_refinery_runs_monthly AS
SELECT
    rm.refinery_id,
    r.name           AS refinery_name,
    r.corporation,
    r.duoarea_code,
    r.capacity_bpd   AS nameplate_bpd,
    rm.month,
    rm.metric,
    rm.value,
    CASE
        WHEN rm.metric = 'crude_runs_bpd' AND r.capacity_bpd > 0
            THEN ROUND((rm.value / r.capacity_bpd * 100)::numeric, 1)
    END              AS implied_utilisation_pct,
    rm.method,
    rm.confidence,
    rm.notes,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM refineries.runs_monthly rm
JOIN refineries.refinery r USING (refinery_id)
LEFT JOIN refineries.sources s ON s.source_id = rm.source_id
ORDER BY rm.refinery_id, rm.metric, rm.month;

CREATE OR REPLACE VIEW refineries.v_refinery_runs_summary AS
SELECT
    rm.refinery_id,
    r.name           AS refinery_name,
    r.capacity_bpd   AS nameplate_bpd,
    rm.metric,
    COUNT(*)                                                       AS n_points,
    MIN(rm.month)                                                  AS month_from,
    MAX(rm.month)                                                  AS month_to,
    ROUND(AVG(rm.value)::numeric, 1)                               AS mean_value,
    ROUND(MIN(rm.value)::numeric, 1)                               AS min_value,
    ROUND(MAX(rm.value)::numeric, 1)                               AS max_value,
    COUNT(*) FILTER (WHERE rm.confidence = 'high')                 AS n_high,
    COUNT(*) FILTER (WHERE rm.confidence = 'medium')               AS n_medium,
    COUNT(*) FILTER (WHERE rm.confidence = 'low')                  AS n_low,
    string_agg(DISTINCT rm.method, ', ' ORDER BY rm.method)        AS methods_used
FROM refineries.runs_monthly rm
JOIN refineries.refinery r USING (refinery_id)
GROUP BY rm.refinery_id, r.name, r.capacity_bpd, rm.metric
ORDER BY rm.refinery_id, rm.metric;

CREATE OR REPLACE VIEW refineries.v_refinery_sources AS
SELECT
    s.refinery_id,
    r.name           AS refinery_name,
    s.source_id,
    s.document_type,
    s.publisher,
    s.title,
    s.url,
    s.published_at,
    s.fetched_at,
    s.notes
FROM refineries.sources s
JOIN refineries.refinery r USING (refinery_id)
ORDER BY s.refinery_id, s.published_at DESC NULLS LAST, s.source_id;

CREATE OR REPLACE VIEW refineries.v_exploration_runs AS
SELECT
    er.run_id,
    er.refinery_id,
    r.name           AS refinery_name,
    er.started_at,
    er.finished_at,
    er.finished_at - er.started_at AS duration,
    er.model,
    er.tool_calls,
    er.tokens_in,
    er.tokens_out,
    er.cost_usd,
    er.status,
    er.error,
    er.summary
FROM refineries.exploration_runs er
JOIN refineries.refinery r USING (refinery_id)
ORDER BY er.started_at DESC;
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        print("refineries views created (or replaced).")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.views
                WHERE table_schema = 'refineries'
                  AND (table_name LIKE 'v_refinery%' OR table_name = 'v_exploration_runs')
                ORDER BY table_name
                """
            )
            views = [r[0] for r in cur.fetchall()]
        print(f"  views present: {views}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
