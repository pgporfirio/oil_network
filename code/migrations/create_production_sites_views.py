"""Inspection views over the `production_sites.*` schema.

Idempotent — safe to re-run. Mirrors the refineries inspection views: gives a
fast overview of what each agent run produced for a producing field.

Views created:

    production_sites.v_field_overview          one row per field, counts + last run
    production_sites.v_field_operators         operators + parent corp + source URL
    production_sites.v_field_grades            grades produced + assay + source URL
    production_sites.v_field_production        sparse step-function production observations (over production_timeseries)
    production_sites.v_field_production_summary per-field, per-metric series stats
    production_sites.v_field_reserves          reserves + source URL
    production_sites.v_field_events            events + source URL
    production_sites.v_field_logistics         logistics links + source URL
    production_sites.v_field_sources           sources, latest first
    production_sites.v_exploration_runs        run log, latest first
    production_sites.v_canonical_field_operators
                                                latest-run row per (field_id, normalized operator_name, start_year)
    production_sites.v_canonical_field_grades  latest-run row per (field_id, normalized grade_name)
    production_sites.v_canonical_events        latest-run row per (field_id, event_type, start_date)
    production_sites.v_canonical_reserves      latest-run row per (field_id, as_of_date, category, commodity)
    production_sites.v_canonical_logistics_links
                                                latest-run row per (field_id, asset_type, normalized asset_name)
    production_sites.v_canonical_sources       latest-run row per (field_id, url)

Run standalone:

    .venv\\Scripts\\python.exe Stage2\\code\\migrations\\create_production_sites_views.py
"""
from __future__ import annotations

import psycopg2


DDL = r"""
CREATE OR REPLACE VIEW production_sites.v_field_overview AS
SELECT
    f.field_id,
    f.name,
    f.basin,
    f.play,
    f.state,
    f.country,
    f.onshore_offshore,
    f.field_type,
    f.status,
    f.discovery_year,
    f.first_production_year,
    f.latitude,
    f.longitude,
    f.last_explored_at,
    (SELECT COUNT(*) FROM production_sites.field_operators fo WHERE fo.field_id = f.field_id) AS n_operators,
    (SELECT COUNT(*) FROM production_sites.field_grades fg  WHERE fg.field_id  = f.field_id) AS n_grades,
    (SELECT COUNT(*) FROM production_sites.production_timeseries pt WHERE pt.field_id = f.field_id) AS n_production_rows,
    (SELECT COUNT(*) FROM production_sites.reserves rs       WHERE rs.field_id  = f.field_id) AS n_reserves,
    (SELECT COUNT(*) FROM production_sites.events ev         WHERE ev.field_id  = f.field_id) AS n_events,
    (SELECT COUNT(*) FROM production_sites.logistics_links ll WHERE ll.field_id = f.field_id) AS n_logistics,
    (SELECT COUNT(*) FROM production_sites.sources s         WHERE s.field_id   = f.field_id) AS n_sources,
    (SELECT MIN(pt.observation_date) FROM production_sites.production_timeseries pt
       WHERE pt.field_id = f.field_id AND pt.metric = 'oil_bpd') AS oil_series_from,
    (SELECT MAX(pt.observation_date) FROM production_sites.production_timeseries pt
       WHERE pt.field_id = f.field_id AND pt.metric = 'oil_bpd') AS oil_series_to,
    (SELECT COUNT(*) FROM production_sites.exploration_runs er
       WHERE er.field_id = f.field_id AND er.status = 'success') AS n_successful_runs,
    (SELECT er.cost_usd    FROM production_sites.exploration_runs er
       WHERE er.field_id = f.field_id ORDER BY er.started_at DESC LIMIT 1) AS last_cost_usd,
    (SELECT er.tool_calls  FROM production_sites.exploration_runs er
       WHERE er.field_id = f.field_id ORDER BY er.started_at DESC LIMIT 1) AS last_tool_calls,
    (SELECT er.summary     FROM production_sites.exploration_runs er
       WHERE er.field_id = f.field_id ORDER BY er.started_at DESC LIMIT 1) AS last_summary
FROM production_sites.field f;

CREATE OR REPLACE VIEW production_sites.v_field_operators AS
SELECT
    fo.field_op_id,
    fo.field_id,
    f.name           AS field_name,
    fo.operator_name,
    fo.parent_corp,
    fo.role,
    fo.share_pct,
    fo.start_year,
    fo.end_year,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM production_sites.field_operators fo
JOIN production_sites.field f USING (field_id)
LEFT JOIN production_sites.sources s ON s.source_id = fo.source_id
ORDER BY fo.field_id, fo.start_year NULLS LAST, fo.operator_name;

CREATE OR REPLACE VIEW production_sites.v_field_grades AS
SELECT
    fg.grade_obs_id,
    fg.field_id,
    f.name           AS field_name,
    fg.grade_name,
    fg.api_gravity,
    fg.sulphur_pct,
    fg.share_pct,
    fg.period_start,
    fg.period_end,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM production_sites.field_grades fg
JOIN production_sites.field f USING (field_id)
LEFT JOIN production_sites.sources s ON s.source_id = fg.source_id
ORDER BY fg.field_id, fg.period_start DESC NULLS LAST, fg.grade_name;

CREATE OR REPLACE VIEW production_sites.v_field_production AS
SELECT
    pt.field_id,
    f.name           AS field_name,
    f.basin,
    f.state,
    pt.observation_date,
    pt.metric,
    pt.value,
    pt.method,
    pt.confidence,
    pt.notes,
    pt.run_id,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM production_sites.production_timeseries pt
JOIN production_sites.field f USING (field_id)
LEFT JOIN production_sites.sources s ON s.source_id = pt.source_id
ORDER BY pt.field_id, pt.metric, pt.observation_date;

CREATE OR REPLACE VIEW production_sites.v_field_production_summary AS
SELECT
    pt.field_id,
    f.name           AS field_name,
    pt.metric,
    COUNT(*)                                                       AS n_observations,
    MIN(pt.observation_date)                                       AS series_from,
    MAX(pt.observation_date)                                       AS series_to,
    ROUND(AVG(pt.value)::numeric, 1)                               AS mean_value,
    ROUND(MIN(pt.value)::numeric, 1)                               AS min_value,
    ROUND(MAX(pt.value)::numeric, 1)                               AS max_value,
    COUNT(*) FILTER (WHERE pt.confidence = 'high')                 AS n_high,
    COUNT(*) FILTER (WHERE pt.confidence = 'medium')               AS n_medium,
    COUNT(*) FILTER (WHERE pt.confidence = 'low')                  AS n_low,
    string_agg(DISTINCT pt.method, ', ' ORDER BY pt.method)        AS methods_used
FROM production_sites.production_timeseries pt
JOIN production_sites.field f USING (field_id)
GROUP BY pt.field_id, f.name, pt.metric
ORDER BY pt.field_id, pt.metric;

CREATE OR REPLACE VIEW production_sites.v_field_reserves AS
SELECT
    rs.reserve_id,
    rs.field_id,
    f.name           AS field_name,
    rs.as_of_date,
    rs.category,
    rs.commodity,
    rs.volume,
    rs.unit,
    rs.notes,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM production_sites.reserves rs
JOIN production_sites.field f USING (field_id)
LEFT JOIN production_sites.sources s ON s.source_id = rs.source_id
ORDER BY rs.field_id, rs.as_of_date DESC NULLS LAST, rs.reserve_id;

CREATE OR REPLACE VIEW production_sites.v_field_events AS
SELECT
    ev.event_id,
    ev.field_id,
    f.name           AS field_name,
    ev.event_type,
    ev.start_date,
    ev.end_date,
    ev.description,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM production_sites.events ev
JOIN production_sites.field f USING (field_id)
LEFT JOIN production_sites.sources s ON s.source_id = ev.source_id
ORDER BY ev.field_id, ev.start_date DESC NULLS LAST, ev.event_id;

CREATE OR REPLACE VIEW production_sites.v_field_logistics AS
SELECT
    ll.link_id,
    ll.field_id,
    f.name           AS field_name,
    ll.asset_type,
    ll.asset_name,
    ll.operator,
    ll.capacity_bpd,
    ll.direction,
    ll.notes,
    s.url            AS source_url,
    s.publisher      AS source_publisher,
    s.document_type  AS source_type
FROM production_sites.logistics_links ll
JOIN production_sites.field f USING (field_id)
LEFT JOIN production_sites.sources s ON s.source_id = ll.source_id
ORDER BY ll.field_id, ll.asset_type, ll.asset_name;

CREATE OR REPLACE VIEW production_sites.v_field_sources AS
SELECT
    s.source_id,
    s.field_id,
    f.name           AS field_name,
    s.document_type,
    s.publisher,
    s.title,
    s.url,
    s.published_at,
    s.fetched_at,
    s.notes
FROM production_sites.sources s
JOIN production_sites.field f USING (field_id)
ORDER BY s.field_id, s.published_at DESC NULLS LAST, s.source_id;

CREATE OR REPLACE VIEW production_sites.v_exploration_runs AS
SELECT
    er.run_id,
    er.field_id,
    f.name           AS field_name,
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
FROM production_sites.exploration_runs er
JOIN production_sites.field f USING (field_id)
ORDER BY er.started_at DESC;

-- Canonical views: pick the latest-run row per natural key per field. Use
-- these when you want "the current best answer" for downstream consumption
-- without inheriting cross-run noise.

CREATE OR REPLACE VIEW production_sites.v_canonical_field_operators AS
SELECT DISTINCT ON (field_id, lower(trim(coalesce(operator_name,''))), start_year)
    fo.*, f.name AS field_name
FROM production_sites.field_operators fo
JOIN production_sites.field f USING (field_id)
WHERE fo.run_id IS NOT NULL
ORDER BY field_id, lower(trim(coalesce(operator_name,''))), start_year, fo.run_id DESC;

CREATE OR REPLACE VIEW production_sites.v_canonical_field_grades AS
SELECT DISTINCT ON (field_id, lower(trim(coalesce(grade_name,''))))
    fg.*, f.name AS field_name
FROM production_sites.field_grades fg
JOIN production_sites.field f USING (field_id)
WHERE fg.run_id IS NOT NULL
ORDER BY field_id, lower(trim(coalesce(grade_name,''))), fg.run_id DESC;

CREATE OR REPLACE VIEW production_sites.v_canonical_events AS
SELECT DISTINCT ON (field_id, event_type, start_date)
    ev.*, f.name AS field_name
FROM production_sites.events ev
JOIN production_sites.field f USING (field_id)
WHERE ev.run_id IS NOT NULL
ORDER BY field_id, event_type, start_date, ev.run_id DESC;

CREATE OR REPLACE VIEW production_sites.v_canonical_reserves AS
SELECT DISTINCT ON (field_id, as_of_date, category, commodity)
    rs.*, f.name AS field_name
FROM production_sites.reserves rs
JOIN production_sites.field f USING (field_id)
WHERE rs.run_id IS NOT NULL
ORDER BY field_id, as_of_date, category, commodity, rs.run_id DESC;

CREATE OR REPLACE VIEW production_sites.v_canonical_logistics_links AS
SELECT DISTINCT ON (field_id, asset_type, lower(trim(coalesce(asset_name,''))))
    ll.*, f.name AS field_name
FROM production_sites.logistics_links ll
JOIN production_sites.field f USING (field_id)
WHERE ll.run_id IS NOT NULL
ORDER BY field_id, asset_type, lower(trim(coalesce(asset_name,''))), ll.run_id DESC;

CREATE OR REPLACE VIEW production_sites.v_canonical_sources AS
SELECT DISTINCT ON (field_id, lower(trim(coalesce(url,''))))
    s.*, f.name AS field_name
FROM production_sites.sources s
JOIN production_sites.field f USING (field_id)
WHERE s.run_id IS NOT NULL
ORDER BY field_id, lower(trim(coalesce(url,''))), s.run_id DESC;
"""


def main() -> None:
    conn = psycopg2.connect(
        host="localhost", dbname="eia_crude",
        user="eia_user", password="eia_password",
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
        print("production_sites views created (or replaced).")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.views
                WHERE table_schema = 'production_sites'
                ORDER BY table_name
                """
            )
            views = [r[0] for r in cur.fetchall()]
        print(f"  views present: {views}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
