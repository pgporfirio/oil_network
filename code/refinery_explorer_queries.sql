-- =====================================================================
-- refinery_explorer_queries.sql
--
-- Inspection queries for the `refineries.*` schema populated by the
-- US Refinery Explorer agent. Pick a refinery, change the
-- :refinery_id at the top, run the section you want.
--
-- Run from psql:
--   psql -h localhost -U eia_user -d eia_crude \
--        -v refinery_id="'ref_american_bradford'" \
--        -f code/refinery_explorer_queries.sql
--
-- Or from DBeaver / VS Code SQL: replace :refinery_id with the literal
-- string 'ref_american_bradford' (with quotes) and run blocks one at
-- a time.
-- =====================================================================

\set refinery_id '\'ref_american_bradford\''

-- ---------------------------------------------------------------------
-- 1. Monthly historical runs — the headline series
-- ---------------------------------------------------------------------
-- One row per month per metric. `value` is bpd for crude_runs_bpd and
-- capacity_bpd, percentage for utilisation_pct. `method` records HOW
-- the value was derived; `confidence` is the agent's own grade.
-- LEFT JOIN onto sources so you can see what backed each point.

SELECT
    rm.month,
    rm.metric,
    rm.value,
    rm.method,
    rm.confidence,
    rm.notes,
    s.url           AS source_url,
    s.document_type AS source_type,
    s.title         AS source_title
FROM refineries.runs_monthly rm
LEFT JOIN refineries.sources s ON s.source_id = rm.source_id
WHERE rm.refinery_id = :refinery_id
ORDER BY rm.metric, rm.month;


-- ---------------------------------------------------------------------
-- 2. Monthly runs pivoted by metric (one column per metric)
-- ---------------------------------------------------------------------
-- Easier to read at a glance. Adds an implied utilisation column when
-- crude_runs_bpd and capacity_bpd are both present.

SELECT
    month,
    MAX(value) FILTER (WHERE metric = 'crude_runs_bpd')   AS crude_runs_bpd,
    MAX(value) FILTER (WHERE metric = 'utilisation_pct')  AS utilisation_pct,
    MAX(value) FILTER (WHERE metric = 'capacity_bpd')     AS capacity_bpd,
    MAX(method)     FILTER (WHERE metric = 'crude_runs_bpd') AS runs_method,
    MAX(confidence) FILTER (WHERE metric = 'crude_runs_bpd') AS runs_confidence
FROM refineries.runs_monthly
WHERE refinery_id = :refinery_id
GROUP BY month
ORDER BY month;


-- ---------------------------------------------------------------------
-- 3. Annual averages from the monthly series — sanity check
-- ---------------------------------------------------------------------
-- Cross against nameplate capacity from refineries.refinery to see if
-- the implied utilisation makes sense (US average is 88-93%).

WITH yearly AS (
    SELECT
        DATE_PART('year', month)::int AS year,
        AVG(value) FILTER (WHERE metric = 'crude_runs_bpd') AS avg_crude_runs_bpd,
        COUNT(*)    FILTER (WHERE metric = 'crude_runs_bpd') AS n_months,
        STRING_AGG(DISTINCT method, ', ') FILTER (WHERE metric = 'crude_runs_bpd') AS methods
    FROM refineries.runs_monthly
    WHERE refinery_id = :refinery_id
    GROUP BY 1
)
SELECT
    y.year,
    y.n_months,
    ROUND(y.avg_crude_runs_bpd::numeric)        AS avg_crude_runs_bpd,
    r.capacity_bpd                              AS nameplate_bpd,
    ROUND(100 * y.avg_crude_runs_bpd::numeric
              / NULLIF(r.capacity_bpd, 0), 1)   AS implied_util_pct,
    y.methods
FROM yearly y
CROSS JOIN refineries.refinery r
WHERE r.refinery_id = :refinery_id
ORDER BY y.year;


-- ---------------------------------------------------------------------
-- 4. Method × confidence breakdown — data-provenance audit
-- ---------------------------------------------------------------------
-- How was each monthly point derived, and how confident is the agent?
-- A series dominated by `low` / `eia_attributed` is a fallback synthesis;
-- one dominated by `high` / `financials_quarterly_split` is closer to
-- ground truth.

SELECT
    metric,
    method,
    confidence,
    COUNT(*) AS n_points,
    MIN(month) AS earliest,
    MAX(month) AS latest
FROM refineries.runs_monthly
WHERE refinery_id = :refinery_id
GROUP BY metric, method, confidence
ORDER BY metric, n_points DESC;


-- ---------------------------------------------------------------------
-- 5. Full refinery dossier — one query per fact table
-- ---------------------------------------------------------------------
-- Run all five blocks and read top-to-bottom.

-- 5a. Master row
SELECT *
FROM refineries.refinery
WHERE refinery_id = :refinery_id;

-- 5b. Process units
SELECT pu.unit_type, pu.capacity_bpd, pu.status, s.url AS source
FROM refineries.process_units pu
LEFT JOIN refineries.sources s ON s.source_id = pu.source_id
WHERE pu.refinery_id = :refinery_id
ORDER BY pu.unit_type;

-- 5c. Slate (crude grades processed)
SELECT period_start, period_end, grade_name, api_gravity, sulphur_pct, share_pct,
       (SELECT url FROM refineries.sources WHERE source_id = sl.source_id) AS source
FROM refineries.slate sl
WHERE refinery_id = :refinery_id
ORDER BY period_start, grade_name;

-- 5d. Events (turnarounds, fires, expansions, ownership changes)
SELECT event_type, start_date, end_date, units_affected, capacity_impact_bpd,
       LEFT(description, 120) AS description_preview,
       (SELECT url FROM refineries.sources WHERE source_id = e.source_id) AS source
FROM refineries.events e
WHERE refinery_id = :refinery_id
ORDER BY start_date NULLS LAST;

-- 5e. Financial periods
SELECT period_start, period_end, period_type,
       throughput_bpd, utilisation_pct, revenue_usd_m, ebitda_usd_m,
       (SELECT url FROM refineries.sources WHERE source_id = f.source_id) AS source
FROM refineries.financials f
WHERE refinery_id = :refinery_id
ORDER BY period_start;


-- ---------------------------------------------------------------------
-- 6. Sources used — every URL the agent visited
-- ---------------------------------------------------------------------
SELECT document_type, publisher, published_at, title, url, notes
FROM refineries.sources
WHERE refinery_id = :refinery_id
ORDER BY document_type, published_at NULLS LAST;


-- ---------------------------------------------------------------------
-- 7. Cross-refinery exploration log — what's been run so far
-- ---------------------------------------------------------------------
-- Drop the WHERE clause to see every exploration run. Useful for
-- monitoring cost + coverage during a sweep.

SELECT
    er.refinery_id,
    r.name,
    er.started_at,
    er.finished_at,
    er.finished_at - er.started_at AS duration,
    er.model,
    er.tool_calls,
    er.cost_usd,
    er.status,
    LEFT(er.summary, 80) AS summary_preview
FROM refineries.exploration_runs er
LEFT JOIN refineries.refinery r USING (refinery_id)
ORDER BY er.started_at DESC;


-- ---------------------------------------------------------------------
-- 8. Coverage summary across all explored refineries
-- ---------------------------------------------------------------------
-- After the full sweep, this is your headline: who's been done, how
-- much was captured per refinery.

SELECT
    er.refinery_id,
    r.name,
    r.capacity_bpd AS nameplate_bpd,
    er.status,
    er.cost_usd,
    (SELECT COUNT(*) FROM refineries.process_units WHERE refinery_id = er.refinery_id) AS n_units,
    (SELECT COUNT(*) FROM refineries.slate         WHERE refinery_id = er.refinery_id) AS n_slate,
    (SELECT COUNT(*) FROM refineries.events        WHERE refinery_id = er.refinery_id) AS n_events,
    (SELECT COUNT(*) FROM refineries.financials    WHERE refinery_id = er.refinery_id) AS n_fin,
    (SELECT COUNT(*) FROM refineries.runs_monthly  WHERE refinery_id = er.refinery_id) AS n_monthly,
    (SELECT COUNT(*) FROM refineries.sources       WHERE refinery_id = er.refinery_id) AS n_sources
FROM refineries.exploration_runs er
JOIN refineries.refinery r USING (refinery_id)
WHERE er.status = 'success'
ORDER BY er.started_at DESC;
