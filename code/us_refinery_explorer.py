"""US Refinery Explorer — Claude Agent SDK agent that builds a per-refinery profile.

For each US refinery in `oil_network.nodes` (subtype='refinery'), the agent:

  1. Pulls EIA monthly aggregates from the local `oil_network.timeseries_data`.
  2. Web-searches and web-fetches 10-Ks, investor decks, press releases, EIA reports.
  3. Records process units, crude slate, turnarounds/events, financial periods,
     and a synthesised monthly crude_runs_bpd series.
  4. Persists everything to schema `refineries.*` (FK to `oil_network.nodes`),
     and also drops a `profile.json` to `outputs/refineries/<refinery_id>/`.

Authentication: relies on the Claude Code CLI subscription login
(`claude login`). No `ANTHROPIC_API_KEY` is required.

Usage from a notebook:

    import asyncio
    from us_refinery_explorer import (
        list_us_refineries, explore_refinery_async,
        persist_to_db, already_explored,
    )

    refineries = list_us_refineries()
    for r in refineries[:3]:
        if already_explored(r["refinery_id"]):
            continue
        buf, meta = await explore_refinery_async(r)
        persist_to_db(buf, r, meta)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from paths import OUTPUTS_DIR


# --------------------------------------------------------------------------- #
# Paths and DB connection                                                     #
# --------------------------------------------------------------------------- #

REFINERIES_OUT = OUTPUTS_DIR / "refineries"

_DB_KW = dict(
    host="localhost",
    dbname="eia_crude",
    user="eia_user",
    password="eia_password",
)

def _conn():
    return psycopg2.connect(**_DB_KW)


# --------------------------------------------------------------------------- #
# Per-refinery buffer (module-level mutable state, reset per exploration)     #
# --------------------------------------------------------------------------- #

_BUFFER: dict[str, Any] = {}


def _reset_buffer(refinery_id: str) -> None:
    _BUFFER.clear()
    _BUFFER.update({
        "refinery_id":       refinery_id,
        "units":             [],
        "slate":             [],
        "events":            [],
        "financials":        [],
        "runs_obs":          [],   # TS observations of aggregate consumption (any date)
        "slate_obs":         [],   # TS observations of slate distribution (any date)
        "sources":           [],
        "commodities":       [],
        "primary_commodity": None,
        "summary":           None,
    })


# --------------------------------------------------------------------------- #
# Tools — registered with the Agent SDK via @tool + create_sdk_mcp_server     #
# --------------------------------------------------------------------------- #

@tool(
    "query_eia_timeseries",
    "Query monthly EIA timeseries from the local oil_network DB. "
    "duoarea_code is the EIA region/district code embedded in the EIA series ID "
    "(e.g. RAP=Appalachian No.1, REC=East Coast, R3B=Texas Gulf Coast, "
    "R10/R20/R30/R40/R50=PADD totals, NUS=USA). "
    "name_filter is an ILIKE fragment on the series name "
    "(e.g. 'crude inputs', 'gross input', 'operating capacity', 'utilization'). "
    "since_date is ISO YYYY-MM-DD (default 2018-01-01). "
    "Returns latest-vintage rows grouped by series, up to 1200 rows total.",
    {"duoarea_code": str, "name_filter": str, "since_date": str},
)
async def query_eia_timeseries(args: dict[str, Any]) -> dict[str, Any]:
    duoarea = (args.get("duoarea_code") or "").strip()
    name_filter = (args.get("name_filter") or "").strip()
    since = (args.get("since_date") or "").strip() or "2018-01-01"
    rows: list[dict[str, Any]] = []
    series_meta: list[dict[str, Any]] = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            # Match duoarea against the EIA timeseries_id pattern: '..._<DUOAREA>_<n>'.
            # We accept the duoarea as a substring surrounded by underscores so 'R10'
            # doesn't accidentally match 'R100' or 'R3B'.
            ts_id_pattern = f"%\\_{duoarea}\\_%" if duoarea else "%"
            cur.execute(
                """
                SELECT DISTINCT ON (td.timeseries_id, td.observation_date)
                       td.timeseries_id, td.observation_date, td.value, t.name, t.unit
                FROM oil_network.timeseries_data td
                JOIN oil_network.timeseries t USING (timeseries_id)
                WHERE t.source = 'eia'
                  AND td.timeseries_id LIKE %s ESCAPE '\\'
                  AND t.name ILIKE %s
                  AND td.observation_date >= %s::date
                ORDER BY td.timeseries_id, td.observation_date, td.saved_date DESC
                LIMIT 1200
                """,
                (ts_id_pattern, f"%{name_filter}%", since),
            )
            seen: dict[str, dict[str, Any]] = {}
            for ts_id, obs, val, nm, unit in cur.fetchall():
                rows.append({
                    "timeseries_id": ts_id,
                    "date": str(obs),
                    "value": float(val) if val is not None else None,
                })
                if ts_id not in seen:
                    seen[ts_id] = {"timeseries_id": ts_id, "name": nm, "unit": unit, "count": 0}
                seen[ts_id]["count"] += 1
            series_meta = list(seen.values())
    except Exception as exc:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"db_error: {exc}"}]}

    payload = json.dumps({
        "series_found": series_meta,
        "row_count": len(rows),
        "rows": rows,
    })
    return {"content": [{"type": "text", "text": payload}]}


@tool(
    "record_unit",
    "Record a process unit at the refinery (e.g. fcc, coker, hydrocracker, "
    "hydrotreater, reformer, alkylation, isomerisation). capacity_bpd is the "
    "downstream feed capacity in barrels per day.",
    {"unit_type": str, "capacity_bpd": int, "source_url": str},
)
async def record_unit(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["units"].append({
        "unit_type": args["unit_type"].lower(),
        "capacity_bpd": args.get("capacity_bpd"),
        "source_url": args.get("source_url"),
    })
    return {"content": [{"type": "text", "text": f"OK: unit {args['unit_type']}"}]}


@tool(
    "record_slate",
    "Record a crude-slate observation for a period. share_pct is the share of "
    "total crude charge represented by this grade. api_gravity and sulphur_pct "
    "describe the grade itself. period_start/end are ISO dates (YYYY-MM-DD). "
    "commodity is the canonical commodity_id (lowercase_with_underscores) this "
    "grade maps to — use a known id like 'wti_midland', 'bakken_light', 'ans', "
    "'mars', etc., or 'crude' if you cannot pin it down. Call record_commodity "
    "FIRST for any new (non-generic) commodity_id you introduce.",
    {
        "period_start": str, "period_end": str, "grade_name": str,
        "api_gravity": float, "sulphur_pct": float, "share_pct": float,
        "commodity": str, "source_url": str,
    },
)
async def record_slate(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["slate"].append(args)
    return {"content": [{"type": "text", "text": f"OK: slate {args['grade_name']}"}]}


@tool(
    "record_event",
    "Record an event: turnaround, fire, expansion, shutdown, ownership_change, "
    "divestiture. Dates ISO (YYYY-MM-DD). units_affected is comma-separated "
    "unit names. capacity_impact_bpd is the temporary capacity reduction if "
    "estimable.",
    {
        "event_type": str, "start_date": str, "end_date": str,
        "units_affected": str, "capacity_impact_bpd": int,
        "description": str, "source_url": str,
    },
)
async def record_event(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["events"].append(args)
    return {"content": [{"type": "text", "text": f"OK: event {args['event_type']}"}]}


@tool(
    "record_financial",
    "Record a financial period from a 10-K, 10-Q, or earnings release. "
    "period_type: 'Q1'|'Q2'|'Q3'|'Q4'|'FY'. throughput_bpd is the refining "
    "segment throughput; utilisation_pct is segment utilisation. Revenue and "
    "EBITDA in USD millions. Allocate to this specific refinery if disclosed; "
    "otherwise record at segment level and note the allocation method.",
    {
        "period_start": str, "period_end": str, "period_type": str,
        "throughput_bpd": int, "utilisation_pct": float,
        "revenue_usd_m": float, "ebitda_usd_m": float, "source_url": str,
    },
)
async def record_financial(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["financials"].append(args)
    return {"content": [{"type": "text", "text": f"OK: fin {args['period_type']} {args['period_start']}"}]}


@tool(
    "record_runs_observation",
    "Record one consumption time-series observation. observation_date is any "
    "ISO date (YYYY-MM-DD); the value applies from that date forward until "
    "the next observation (last-observation-carried-forward semantics, same "
    "convention as oil_network.timeseries_data). For EIA monthly data, use "
    "the first of the month; for annual disclosures use the period_end; for "
    "press-release / investor-day events use the actual disclosure date. "
    "metric is one of 'crude_runs_bpd', 'utilisation_pct', 'capacity_bpd' "
    "(all rate-typed). method explains how the value was derived "
    "('eia_attributed', 'financials_quarterly_split', "
    "'news_turnaround_adjusted', 'capacity_utilisation_baseline', "
    "'investor_presentation', 'press_release'). confidence: 'high'|'medium'|"
    "'low'. notes can carry the allocation share or any caveat. commodity is "
    "the canonical commodity_id this throughput pertains to — use 'crude' "
    "(generic) unless the source is specific to a particular grade.",
    {
        "observation_date": str, "metric": str, "value": float,
        "method": str, "confidence": str, "notes": str,
        "commodity": str, "source_url": str,
    },
)
async def record_runs_observation(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["runs_obs"].append(args)
    return {"content": [{"type": "text", "text": f"OK: {args['metric']} {args['observation_date']}"}]}


@tool(
    "record_source",
    "Register a source URL the agent consulted. document_type: '10-K' | '10-Q' | "
    "'press_release' | 'news' | 'eia' | 'investor_presentation' | 'trade_press' | "
    "'sustainability_report' | 'other'. published_date is the publication date "
    "(YYYY-MM-DD) if available, else empty string.",
    {
        "url": str, "title": str, "publisher": str,
        "document_type": str, "published_date": str, "notes": str,
    },
)
async def record_source(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["sources"].append(args)
    return {"content": [{"type": "text", "text": f"OK: source {args['url'][:60]}"}]}


@tool(
    "list_commodities",
    "List the canonical commodity catalogue so you can REUSE existing ids "
    "instead of creating new ones. Returns rows from refineries.commodities "
    "(the staging table this agent writes to) AND oil_network.commodities "
    "(the master catalogue). Each row carries commodity_id, description, "
    "sweet_sour, density_class, and api/sulfur ranges. **Always call this "
    "BEFORE record_commodity or record_slate** — if a grade you're about to "
    "record already exists in either catalogue, use that id verbatim and skip "
    "record_commodity. Only introduce a new id when no existing entry matches.",
    {},
)
async def list_commodities(args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    rows: list[dict[str, Any]] = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 'refineries' AS source, commodity, description,
                       sweet_sour, density_class,
                       api_gravity_min, api_gravity_max,
                       sulfur_pct_min, sulfur_pct_max,
                       region, typical_basin
                FROM refineries.commodities
                UNION ALL
                SELECT 'oil_network' AS source, commodity, description,
                       sweet_sour, density_class,
                       api_gravity_min, api_gravity_max,
                       sulfur_pct_min, sulfur_pct_max,
                       region, typical_basin
                FROM oil_network.commodities
                ORDER BY source, commodity
                """
            )
            for row in cur.fetchall():
                rows.append({
                    "source": row[0], "commodity": row[1], "description": row[2],
                    "sweet_sour": row[3], "density_class": row[4],
                    "api_gravity_min": float(row[5]) if row[5] is not None else None,
                    "api_gravity_max": float(row[6]) if row[6] is not None else None,
                    "sulfur_pct_min":  float(row[7]) if row[7] is not None else None,
                    "sulfur_pct_max":  float(row[8]) if row[8] is not None else None,
                    "region": row[9], "typical_basin": row[10],
                })
    except Exception as exc:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"db_error: {exc}"}]}
    return {"content": [{"type": "text", "text": json.dumps({"count": len(rows), "rows": rows})}]}


@tool(
    "record_slate_observation",
    "Override the derived slate distribution at a specific date with date-"
    "level evidence (turnaround/supply-shock/contract change/etc.). "
    "observation_date is any ISO date (YYYY-MM-DD) — the value applies from "
    "that date forward until the next slate observation (LOCF). commodity is "
    "a canonical id (call list_commodities first). probability ∈ [0, 1] — "
    "for a single dominant grade use 1.0; for a mix call this multiple times "
    "with each commodity's share, ensuring the per-date sum is ~1.0. "
    "method describes the evidence ('agent_override' default; can also be "
    "'declared_in_press_release', 'inferred_from_event'). confidence: "
    "'high'|'medium'|'low'. Only use this when you have date-specific "
    "evidence; rely on record_slate (period-level) for the baseline.",
    {
        "observation_date": str, "commodity": str, "probability": float,
        "method": str, "confidence": str, "notes": str, "source_url": str,
    },
)
async def record_slate_observation(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["slate_obs"].append(args)
    return {"content": [{"type": "text", "text": f"OK: slate_obs {args.get('observation_date')} {args.get('commodity')} p={args.get('probability')}"}]}


@tool(
    "record_commodity",
    "Register a crude commodity/grade you have identified for this refinery's "
    "slate. commodity is a stable lowercase_with_underscores identifier "
    "(e.g. 'wti_midland', 'bakken_light', 'ans', 'mars', 'eagle_ford_light'). "
    "Use established names where possible — they will later be reconciled "
    "against the master commodities catalogue. description is a 1-line "
    "human-readable label. sweet_sour: 'sweet'|'medium_sour'|'sour'. "
    "density_class: 'very_light'|'light'|'medium'|'heavy'. API gravity and "
    "sulphur ranges where known (leave 0.0 if unknown). region/typical_basin "
    "if it has a clear origin. **Call this BEFORE record_slate** for any "
    "non-generic grade you introduce — the slate row will FK to this entry.",
    {
        "commodity": str, "description": str,
        "sweet_sour": str, "density_class": str,
        "api_gravity_min": float, "api_gravity_max": float,
        "sulfur_pct_min": float, "sulfur_pct_max": float,
        "region": str, "typical_basin": str,
    },
)
async def record_commodity(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["commodities"].append(args)
    return {"content": [{"type": "text", "text": f"OK: commodity {args['commodity']}"}]}


@tool(
    "finalise",
    "Declare the exploration complete. summary is 2-3 sentences describing "
    "what was found and what remains uncertain. primary_commodity is the "
    "most-likely single commodity_id for this refinery's slate (the dominant "
    "grade if known) — set to 'crude' (generic) if you cannot defensibly "
    "single one out. Should match an id you've registered via record_commodity, "
    "or 'crude'.",
    {"summary": str, "primary_commodity": str},
)
async def finalise(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["summary"] = args["summary"]
    pc = (args.get("primary_commodity") or "").strip().lower()
    _BUFFER["primary_commodity"] = pc or "crude"
    return {"content": [{"type": "text", "text": f"finalised. primary_commodity={_BUFFER['primary_commodity']}"}]}


_explorer_server = create_sdk_mcp_server(
    name="refexp",
    version="1.0.0",
    tools=[
        query_eia_timeseries,
        list_commodities,
        record_unit, record_slate, record_event,
        record_financial, record_runs_observation, record_source,
        record_commodity, record_slate_observation,
        finalise,
    ],
)


# --------------------------------------------------------------------------- #
# System + user prompts                                                       #
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are a refining industry research analyst. For one US refinery, you build a comprehensive profile from public sources.

You have these tools:

- WebSearch — Google-style web search.
- WebFetch — fetch a URL and read its content (works on HTML and PDFs).
- query_eia_timeseries — read monthly EIA series from the local DB. EIA refinery data is regionally aggregated (PADD or refining district), NOT per-plant. The refinery's `duoarea_code` (e.g. RAP, REC, R3B) maps directly to the EIA district series stored locally; also R10/R20/R30/R40/R50 for PADD totals, NUS for USA. Typical `name_filter` values: 'crude inputs', 'gross input', 'operating capacity', 'utilization'. The local DB has 16 refining-input series covering 2015-2026 — ONE call per metric is usually enough; do NOT WebFetch EIA pages when the local DB returns rows.
- list_commodities — return the canonical commodity catalogue (refineries.commodities + oil_network.commodities). **Call this FIRST**, before any record_commodity / record_slate / record_slate_observation. Reuse existing ids; only introduce a new id when no existing entry matches.
- record_unit, record_slate, record_event, record_financial, record_runs_observation, record_source — structured emit. Call as you find facts. Data is buffered and persisted at the end.
- record_commodity — register a crude grade you have identified (e.g. 'wti_midland', 'bakken_light'). Call BEFORE record_slate for any non-generic grade. Only when not already in the catalogue (check via list_commodities first).
- record_slate_observation — date-specific slate override. Use when you have direct evidence that the slate at a specific date differed from the period baseline (turnaround swap, supply disruption, contract change). The baseline distribution is **derived automatically** from your period-level record_slate entries; you only need to override when you have date-level evidence.
- finalise — call exactly once at the end with a 2-3 sentence summary AND the refinery's primary_commodity (most-likely dominant grade, or 'crude' if you can't defensibly pin one down).

**Time-series convention (important):** consumption and slate observations are stored as **step-function time series with last-observation-carried-forward (LOCF) semantics**, NOT as monthly buckets. A value at `observation_date='2024-01-01'` applies from that date until the next observation. So:
- EIA monthly data → use `2024-01-01`, `2024-02-01`, etc. (first of each month).
- Annual disclosures from a 10-K → use the period-end date (e.g. `2024-12-31`).
- Investor-day or press-release evidence → use the actual disclosure date (e.g. `2024-05-15`).
- A turnaround that runs March 1–31 affecting throughput → record observations at `2024-03-01` (reduced rate) and `2024-04-01` (back to normal). LOCF carries each value until the next observation, so you do NOT need to emit a row for every day.

YOUR GOAL — fill in, to the best of your ability:

1. **Process units.** FCC, coker, hydrocracker, hydrotreater, reformer, alkylation, isomerisation — presence and per-unit capacity in bpd. Sources: EIA refcap report (linked from eia.gov), the operator's 10-K (Refining Operations section), investor presentations.

2. **Crude slate.**
   a. **First, call list_commodities** to see the existing catalogue (both refineries.commodities and oil_network.commodities). Reuse existing ids verbatim — if a grade you're about to record already exists under any spelling, use that id. Only call record_commodity to introduce a new id when nothing in either catalogue matches.
   b. **Period-level slate** via record_slate — cite share-percentages where disclosed. Look in 10-K business descriptions, sustainability reports, EIA crude-by-rail receipts, trade press (Argus, Reuters, S&P Platts). Heavy/medium/light is acceptable when named grades aren't disclosed. Set the `commodity` field on each record_slate call to a canonical id from step (a), or 'crude' if you cannot pin it down.
   c. **Month-specific slate (optional)** via record_monthly_slate — only when you have direct evidence that a specific month's slate differed from the period baseline (a press release announcing a switch in feed, a turnaround that forced substitution, a documented supply disruption). The agent does NOT need to emit per-month entries for the routine baseline — the system derives a per-month probability distribution automatically from your period-level record_slate entries. Use record_monthly_slate sparingly, only when month-level evidence exists.
   d. In finalise, set primary_commodity to the single most-likely dominant grade — or 'crude' if you can't defensibly single one out. Default to 'crude' rather than guess.

3. **Owner and operator.** Legal entity vs operating brand. Parent corporation. Any ownership changes in the past 10 years (joint ventures, MLPs, divestitures).

4. **Turnarounds and events** in the past 5 years — **CONFIRMED ONLY**. Record events that are explicitly cited by name in a primary source (IIR turnaround calendar, Reuters/Bloomberg news, CSB incident report, operator press release, 10-K MD&A). DO NOT infer turnarounds from dips in EIA district throughput — district-level dips reflect aggregate behaviour across many refineries and are not attributable to one plant. Include fires/incidents, expansions/debottleneck projects, shutdowns, ownership changes.

5. **Financials.** From 10-Ks/10-Qs of the parent corporation, extract refining-segment throughput, utilisation, revenue, EBITDA by quarter. If the parent reports per-refinery data (rare), record at the refinery level. Otherwise record at segment level and note that allocation is by capacity share.

6. **crude_runs_bpd time series** (step function, LOCF). Build observations back as far as you can defend, using these methods in order of preference:
   - **eia_attributed** — when EIA gives PADD-level monthly runs, allocate to this plant by capacity share: this_plant_runs = padd_runs × (this_plant_capacity / padd_total_capacity). Emit one row per month with `observation_date = first of month`. Note the allocation share in the `notes` field.
   - **financials_quarterly_split** — when a 10-K/10-Q discloses quarterly throughput, you can EITHER emit one row at the period_end date (LOCF carries through the quarter) OR three monthly rows. Prefer one row per disclosed period for compactness.
   - **financials_annual_with_tars** — when only annual throughput is disclosed, combine it with turnaround events to back out a step series: solve `annual_avg × 365 = N×days_normal + 0.5N×days_TAR` for the normal-ops rate N, then emit observations at start-of-year (N), TAR start (reduced), TAR end (back to N). LOCF turns 3-4 rows into a full year of derived rates.
   - **news_turnaround_adjusted** — when a turnaround is reported, emit an observation at the start date (reduced rate) and another at the end date (recovered rate).
   - **capacity_utilisation_baseline** — fallback: runs = capacity × regional_utilisation at the dates of the underlying utilisation series.
   - Always set confidence: 'high' (directly disclosed), 'medium' (one transformation), 'low' (multiple imputations).
   - **You do NOT need to emit one row per month.** Emit observations at the dates where evidence supports them — sparse is fine; LOCF fills the gaps.

WORKING PROCEDURE:

1. Start with **WebSearch** for "{corporation} 10-K most recent" or "{corporation} annual report 2024". Find the SEC EDGAR filing or investor relations page. Use **WebFetch** to open the 10-K PDF or HTML.

2. Inside the 10-K, look for the Refining Operations section and any per-refinery disclosures.

3. **WebSearch** for the specific refinery by site name + "turnaround OR maintenance OR expansion" over the past 5 years.

4. **query_eia_timeseries** with the refinery's duoarea_code to pull monthly PADD-level runs and utilisation.

5. **record_source** every URL you visit. **record_unit / record_slate / record_event / record_financial / record_monthly** as you find facts. Be granular — many small records beat one big one.

6. **finalise** at the end with a 2-3 sentence summary.

RULES:

- BRITISH SPELLING in any natural-language strings ("modelled", "utilisation", "behaviour", "colour", "organisation").
- BE SCEPTICAL. If two sources conflict, record both with their source URLs. Don't fabricate.
- GAPS ARE VALUABLE. In the finalise summary, explicitly state what you couldn't find.
- Each record_* call should carry a source_url where possible. If the fact is inferred (e.g. capacity-allocated EIA data), set source_url to the EIA URL used.
- BUDGET: aim for 30-50 tool calls total, HARD CAP 70. After 60 tool calls, prioritise calling finalise even if some sections are sparse — gaps are valuable when honestly declared.
- ONE WebFetch per source: extract everything you need on the first read, don't return to the same URL.
- Prefer the local query_eia_timeseries tool over WebFetching EIA pages — the same data is already in our DB.
"""


USER_PROMPT_TEMPLATE = """Research this refinery and build its profile.

Refinery: {name}
Refinery ID: {refinery_id}
Corporation: {corporation}
Operator: {operator}
Site: {site}
State: {state}
PADD: {padd}
Refining district: {rdist_label}
EIA duoarea code: {duoarea_code}
Known capacity: {capacity_bpd_str} bpd
Existing process-unit flags in our DB: {existing_units}

Build the profile. Cover: process units, crude slate, owner/operator, turnarounds/events from the past 5 years, financial throughput per quarter for the past 3 years, and a monthly crude_runs_bpd series back as far as you can defensibly extend. Save every source via record_source. End with finalise.
"""


# --------------------------------------------------------------------------- #
# Loading refineries from the DB                                              #
# --------------------------------------------------------------------------- #

def list_us_refineries() -> list[dict[str, Any]]:
    """Return all US refinery rows (subtype='refinery', country='US')."""
    sql = """
        SELECT n.node_id, a.name, l.padd, l.state,
               a.attributes->'configuration' AS cfg
        FROM oil_network.nodes n
        JOIN oil_network.assets a   ON a.asset_id = n.asset_id
        LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
        WHERE a.node_subtype = 'refinery'
          AND l.country = 'US'
        ORDER BY l.padd NULLS LAST, l.state, a.name
    """
    out: list[dict[str, Any]] = []
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        for node_id, name, padd, state, cfg in cur.fetchall():
            cfg = cfg or {}
            cap = cfg.get("capacity_bpd") or cfg.get("capacity_bd")
            flags = [k for k in ("has_fcc", "has_coker", "has_hydrocracker") if cfg.get(k)]
            out.append({
                "refinery_id": node_id,
                "name":        name,
                "padd":        padd,
                "state":       state,
                "corporation": cfg.get("corporation"),
                "operator":    cfg.get("operator"),
                "site":        cfg.get("site"),
                "rdist_label": cfg.get("rdist_label"),
                "capacity_bpd": cap,
                "duoarea_code": cfg.get("duoarea_code"),
                "existing_units": ", ".join(flags) or "unknown",
            })
    return out


# --------------------------------------------------------------------------- #
# Agent invocation                                                            #
# --------------------------------------------------------------------------- #

async def explore_refinery_async(
    refinery: dict[str, Any],
    model: str = "claude-sonnet-4-6",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the agent on one refinery. Returns (buffer, run_meta)."""
    _reset_buffer(refinery["refinery_id"])

    cap = refinery.get("capacity_bpd") or 0
    fields = {
        **refinery,
        "capacity_bpd_str": f"{cap:,}",
        "corporation":   refinery.get("corporation") or "(unknown)",
        "operator":      refinery.get("operator") or "(unknown)",
        "site":          refinery.get("site") or refinery.get("name", ""),
        "state":         refinery.get("state") or "",
        "padd":          refinery.get("padd") or "",
        "rdist_label":   refinery.get("rdist_label") or "",
        "duoarea_code":  refinery.get("duoarea_code") or "",
    }
    user_prompt = USER_PROMPT_TEMPLATE.format(**fields)

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"refexp": _explorer_server},
        allowed_tools=[
            "WebSearch", "WebFetch",
            "mcp__refexp__query_eia_timeseries",
            "mcp__refexp__list_commodities",
            "mcp__refexp__record_unit",
            "mcp__refexp__record_slate",
            "mcp__refexp__record_event",
            "mcp__refexp__record_financial",
            "mcp__refexp__record_runs_observation",
            "mcp__refexp__record_source",
            "mcp__refexp__record_commodity",
            "mcp__refexp__record_slate_observation",
            "mcp__refexp__finalise",
        ],
        permission_mode="bypassPermissions",  # headless across many refineries
    )

    started = datetime.utcnow()
    tool_calls = 0
    tokens_in = tokens_out = 0
    cost_usd = 0.0
    error: str | None = None
    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        tool_calls += 1
            elif isinstance(message, ResultMessage):
                tokens_in  = getattr(message, "input_tokens",  None) or tokens_in
                tokens_out = getattr(message, "output_tokens", None) or tokens_out
                cost_usd   = getattr(message, "total_cost_usd", None) or cost_usd
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)

    finished = datetime.utcnow()
    meta = {
        "model":       model,
        "started_at":  started,
        "finished_at": finished,
        "tool_calls":  tool_calls,
        "tokens_in":   tokens_in,
        "tokens_out":  tokens_out,
        "cost_usd":    cost_usd,
        "status":      "failed" if error else ("partial" if _BUFFER["summary"] is None else "success"),
        "error":       error,
    }
    return dict(_BUFFER), meta


# --------------------------------------------------------------------------- #
# Persistence                                                                 #
# --------------------------------------------------------------------------- #

def already_explored(refinery_id: str) -> bool:
    """True if a successful exploration_runs row exists."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM refineries.exploration_runs "
            "WHERE refinery_id = %s AND status = 'success' LIMIT 1",
            (refinery_id,),
        )
        return cur.fetchone() is not None


def _derive_slate_distribution_ts(
    refinery_id: str,
    primary_commodity: str,
    run_id: int | None,
    cur,
) -> int:
    """Derive `refineries.slate_distribution_ts` rows for a refinery.

    For every observation_date in `refineries.runs_ts` for this refinery:

      1. If `slate_distribution_ts` already has agent_override rows for
         (refinery, observation_date), leave them alone.
      2. Otherwise find slate observations whose [period_start, period_end]
         covers the observation_date AND which have a non-NULL canonical
         commodity. If a single commodity appears in multiple overlapping
         rows, keep the row with the largest run_id (most recent agent run).
      3. Normalise share_pct over the surviving rows so the per-date sum is
         1.0. Insert one row per commodity with
         method='derived_from_slate_period'.
      4. If no canonicalised slate rows cover the date, insert a single
         fallback row at `primary_commodity` with probability=1.0 and
         method='fallback_primary'.

    Returns the count of rows touched. Idempotent.
    """
    # 1. Dates already pinned by agent_override.
    cur.execute(
        """
        SELECT DISTINCT observation_date
        FROM refineries.slate_distribution_ts
        WHERE refinery_id = %s AND method = 'agent_override'
        """,
        (refinery_id,),
    )
    overridden_dates = {row[0] for row in cur.fetchall()}

    # 2. Pull every observation_date that has a runs_ts row.
    cur.execute(
        """
        SELECT DISTINCT observation_date
        FROM refineries.runs_ts
        WHERE refinery_id = %s
        ORDER BY observation_date
        """,
        (refinery_id,),
    )
    dates = [row[0] for row in cur.fetchall() if row[0] not in overridden_dates]
    if not dates:
        return 0

    # 3. Canonicalised slate observations.
    cur.execute(
        """
        SELECT period_start, period_end, commodity, share_pct,
               COALESCE(run_id, 0) AS run_id
        FROM refineries.slate
        WHERE refinery_id = %s AND commodity IS NOT NULL
        """,
        (refinery_id,),
    )
    slate_rows = cur.fetchall()

    touched = 0
    for obs_date in dates:
        covering = [
            (commodity, float(share_pct or 0.0), run_id_val)
            for (period_start, period_end, commodity, share_pct, run_id_val) in slate_rows
            if period_start <= obs_date and (period_end is None or obs_date <= period_end)
        ]

        best_by_commodity: dict[str, tuple[float, int]] = {}
        for commodity, share, run_id_val in covering:
            existing = best_by_commodity.get(commodity)
            if existing is None or run_id_val > existing[1]:
                best_by_commodity[commodity] = (share, run_id_val)

        if not best_by_commodity:
            # Fallback: primary_commodity at p=1.0, plus clear stale rows.
            cur.execute(
                """
                INSERT INTO refineries.slate_distribution_ts
                    (refinery_id, observation_date, commodity, probability,
                     method, run_id)
                VALUES (%s, %s, %s, 1.0, 'fallback_primary', %s)
                ON CONFLICT (refinery_id, observation_date, commodity) DO UPDATE SET
                    probability = EXCLUDED.probability,
                    method      = EXCLUDED.method,
                    run_id      = EXCLUDED.run_id
                """,
                (refinery_id, obs_date, primary_commodity, run_id),
            )
            cur.execute(
                """
                DELETE FROM refineries.slate_distribution_ts
                WHERE refinery_id = %s AND observation_date = %s
                  AND commodity <> %s
                  AND method IN ('derived_from_slate_period', 'fallback_primary')
                """,
                (refinery_id, obs_date, primary_commodity),
            )
            touched += 1
            continue

        total = sum(share for share, _ in best_by_commodity.values())
        if total <= 0:
            cur.execute(
                """
                INSERT INTO refineries.slate_distribution_ts
                    (refinery_id, observation_date, commodity, probability,
                     method, run_id)
                VALUES (%s, %s, %s, 1.0, 'fallback_primary', %s)
                ON CONFLICT (refinery_id, observation_date, commodity) DO UPDATE SET
                    probability = EXCLUDED.probability,
                    method      = EXCLUDED.method,
                    run_id      = EXCLUDED.run_id
                """,
                (refinery_id, obs_date, primary_commodity, run_id),
            )
            touched += 1
            continue

        cur.execute(
            """
            DELETE FROM refineries.slate_distribution_ts
            WHERE refinery_id = %s AND observation_date = %s
              AND method IN ('derived_from_slate_period', 'fallback_primary')
            """,
            (refinery_id, obs_date),
        )

        for commodity, (share, _) in best_by_commodity.items():
            probability = round(share / total, 4)
            cur.execute(
                """
                INSERT INTO refineries.slate_distribution_ts
                    (refinery_id, observation_date, commodity, probability,
                     method, run_id)
                VALUES (%s, %s, %s, %s, 'derived_from_slate_period', %s)
                ON CONFLICT (refinery_id, observation_date, commodity) DO UPDATE SET
                    probability = EXCLUDED.probability,
                    method      = EXCLUDED.method,
                    run_id      = EXCLUDED.run_id
                """,
                (refinery_id, obs_date, commodity, probability, run_id),
            )
            touched += 1

    return touched


def derive_slate_distribution_ts(refinery_id: str) -> int:
    """Public re-derivation helper. Resolves primary_commodity from the DB and
    runs the derivation under a fresh transaction. Use this to backfill the
    distribution table after the slate has been updated outside an agent run.
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT primary_commodity FROM refineries.refinery WHERE refinery_id = %s",
            (refinery_id,),
        )
        row = cur.fetchone()
        primary = (row[0] if row else None) or "crude"
        cur.execute(
            "INSERT INTO refineries.commodities (commodity) VALUES (%s) "
            "ON CONFLICT (commodity) DO NOTHING",
            (primary,),
        )
        touched = _derive_slate_distribution_ts(refinery_id, primary, None, cur)
    return touched


def _derive_runs_by_grade_ts(refinery_id: str, run_id: int | None, cur) -> int:
    """Decompose each `runs_ts` row into per-commodity rows using the
    probability distribution from `slate_distribution_ts`. Aggregate value ×
    probability = per-commodity value.

    Returns the number of derived rows touched. Idempotent. Rows already
    marked source='observed' are preserved.
    """
    cur.execute(
        """
        SELECT rt.observation_date, rt.metric, rt.value,
               sd.commodity, sd.probability
        FROM refineries.runs_ts rt
        JOIN refineries.slate_distribution_ts sd
          ON sd.refinery_id      = rt.refinery_id
         AND sd.observation_date = rt.observation_date
        WHERE rt.refinery_id = %s AND rt.value IS NOT NULL
        ORDER BY rt.observation_date, rt.metric, sd.commodity
        """,
        (refinery_id,),
    )
    rows = cur.fetchall()

    touched = 0
    for obs_date, metric, agg_value, commodity, probability in rows:
        per_grade = float(agg_value) * float(probability)
        cur.execute(
            """
            INSERT INTO refineries.runs_by_grade_ts
                (refinery_id, observation_date, metric, commodity, value,
                 source, run_id)
            VALUES (%s, %s, %s, %s, %s, 'derived_from_slate_dist', %s)
            ON CONFLICT (refinery_id, observation_date, metric, commodity) DO UPDATE SET
                value  = EXCLUDED.value,
                source = EXCLUDED.source,
                run_id = EXCLUDED.run_id
            WHERE refineries.runs_by_grade_ts.source <> 'observed'
            """,
            (refinery_id, obs_date, metric, commodity, per_grade, run_id),
        )
        touched += 1
    return touched


def derive_runs_by_grade_ts(refinery_id: str) -> int:
    """Public re-derivation helper for runs_by_grade_ts. Decomposes each
    refinery observation's aggregate throughput into per-commodity rows using
    the current slate_distribution_ts. Run after the distribution has been
    (re-)derived or after new runs_ts rows have been ingested.
    """
    with _conn() as conn, conn.cursor() as cur:
        touched = _derive_runs_by_grade_ts(refinery_id, None, cur)
    return touched


def persist_to_db(
    buf: dict[str, Any],
    refinery: dict[str, Any],
    meta: dict[str, Any],
) -> None:
    """Upsert the buffer into refineries.* + write outputs/refineries/<id>/profile.json.

    Ordering matters here:
      1.  refinery row (so all FK refs to refineries.refinery work)
      2.  exploration_runs row (RETURNING run_id, so subsequent rows can tag it)
      3.  commodities (so slate / runs_monthly FK refs work)
      4.  sources (so {unit,slate,event,fin,monthly}.source_id refs work)
      5.  units, slate, events, financials, monthly
      6.  UPDATE refinery.primary_commodity from finalise (after the commodity exists)
    """
    rid = refinery["refinery_id"]
    primary_commodity = (buf.get("primary_commodity") or "crude").strip().lower() or "crude"

    with _conn() as conn:
        with conn.cursor() as cur:
            # 1. Refinery master row (primary_commodity gets defaulted to 'crude'
            #    by the schema; we set it explicitly at step 6 once we know the
            #    commodity row exists).
            cur.execute(
                """
                INSERT INTO refineries.refinery
                    (refinery_id, name, corporation, operator, site, state, padd,
                     rdist_label, capacity_bpd, duoarea_code, last_explored_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (refinery_id) DO UPDATE SET
                    name             = EXCLUDED.name,
                    corporation      = EXCLUDED.corporation,
                    operator         = EXCLUDED.operator,
                    site             = EXCLUDED.site,
                    state            = EXCLUDED.state,
                    padd             = EXCLUDED.padd,
                    rdist_label      = EXCLUDED.rdist_label,
                    capacity_bpd     = EXCLUDED.capacity_bpd,
                    duoarea_code     = EXCLUDED.duoarea_code,
                    last_explored_at = now()
                """,
                (
                    rid, refinery.get("name"), refinery.get("corporation"),
                    refinery.get("operator"), refinery.get("site"),
                    refinery.get("state"), refinery.get("padd"),
                    refinery.get("rdist_label"), refinery.get("capacity_bpd"),
                    refinery.get("duoarea_code"),
                ),
            )

            # 2. Exploration run audit — insert FIRST so subsequent rows can
            #    carry run_id back to this audit row.
            cur.execute(
                """
                INSERT INTO refineries.exploration_runs
                    (refinery_id, started_at, finished_at, model, tool_calls,
                     tokens_in, tokens_out, cost_usd, status, error, summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING run_id
                """,
                (
                    rid, meta.get("started_at"), meta.get("finished_at"),
                    meta.get("model"), meta.get("tool_calls"),
                    meta.get("tokens_in"), meta.get("tokens_out"),
                    meta.get("cost_usd"), meta.get("status"),
                    meta.get("error"), buf.get("summary"),
                ),
            )
            run_id = cur.fetchone()[0]

            # 3. Commodities — staging table that will later be reconciled with
            #    oil_network.commodities. ON CONFLICT keeps the existing row but
            #    fills in null columns from the new observation (richer wins).
            for c in buf.get("commodities", []):
                cid = (c.get("commodity") or "").strip().lower()
                if not cid:
                    continue
                cur.execute(
                    """
                    INSERT INTO refineries.commodities
                        (commodity, description, sweet_sour, density_class,
                         api_gravity_min, api_gravity_max,
                         sulfur_pct_min, sulfur_pct_max,
                         region, typical_basin, discovered_in_run)
                    VALUES (%s, %s, %s, %s,
                            NULLIF(%s, 0), NULLIF(%s, 0),
                            NULLIF(%s, 0), NULLIF(%s, 0),
                            %s, %s, %s)
                    ON CONFLICT (commodity) DO UPDATE SET
                        description     = COALESCE(refineries.commodities.description,     EXCLUDED.description),
                        sweet_sour      = COALESCE(refineries.commodities.sweet_sour,      EXCLUDED.sweet_sour),
                        density_class   = COALESCE(refineries.commodities.density_class,   EXCLUDED.density_class),
                        api_gravity_min = COALESCE(refineries.commodities.api_gravity_min, EXCLUDED.api_gravity_min),
                        api_gravity_max = COALESCE(refineries.commodities.api_gravity_max, EXCLUDED.api_gravity_max),
                        sulfur_pct_min  = COALESCE(refineries.commodities.sulfur_pct_min,  EXCLUDED.sulfur_pct_min),
                        sulfur_pct_max  = COALESCE(refineries.commodities.sulfur_pct_max,  EXCLUDED.sulfur_pct_max),
                        region          = COALESCE(refineries.commodities.region,          EXCLUDED.region),
                        typical_basin   = COALESCE(refineries.commodities.typical_basin,   EXCLUDED.typical_basin)
                    """,
                    (
                        cid, c.get("description"),
                        c.get("sweet_sour") or None, c.get("density_class") or None,
                        c.get("api_gravity_min") or 0, c.get("api_gravity_max") or 0,
                        c.get("sulfur_pct_min") or 0, c.get("sulfur_pct_max") or 0,
                        c.get("region") or None, c.get("typical_basin") or None,
                        run_id,
                    ),
                )

            # Safety net — guarantee primary_commodity exists before we point at it.
            cur.execute(
                """
                INSERT INTO refineries.commodities (commodity, discovered_in_run)
                VALUES (%s, %s) ON CONFLICT (commodity) DO NOTHING
                """,
                (primary_commodity, run_id),
            )

            def _norm_commodity(value: Any) -> str:
                """Normalise a commodity arg from a record_* tool: blank → 'crude'.

                Also auto-inserts unknown commodities as stubs so the FK ref holds.
                """
                cv = (str(value or "").strip().lower()) or "crude"
                cur.execute(
                    """
                    INSERT INTO refineries.commodities (commodity, discovered_in_run)
                    VALUES (%s, %s) ON CONFLICT (commodity) DO NOTHING
                    """,
                    (cv, run_id),
                )
                return cv

            # 4. Sources — capture source_id keyed by URL.
            source_ids: dict[str, int] = {}
            for s in buf.get("sources", []):
                cur.execute(
                    """
                    INSERT INTO refineries.sources
                        (refinery_id, url, title, publisher, document_type,
                         published_at, notes)
                    VALUES (%s, %s, %s, %s, %s,
                            NULLIF(%s, '')::date, %s)
                    RETURNING source_id
                    """,
                    (
                        rid, s.get("url"), s.get("title"), s.get("publisher"),
                        s.get("document_type"),
                        (s.get("published_date") or "").strip(),
                        s.get("notes"),
                    ),
                )
                source_ids[s.get("url") or ""] = cur.fetchone()[0]

            def _src(url: str | None) -> int | None:
                return source_ids.get(url or "")

            # 5a. Process units.
            for u in buf.get("units", []):
                cur.execute(
                    """
                    INSERT INTO refineries.process_units
                        (refinery_id, unit_type, capacity_bpd, source_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (refinery_id, unit_type) DO UPDATE SET
                        capacity_bpd = EXCLUDED.capacity_bpd,
                        source_id    = EXCLUDED.source_id
                    """,
                    (rid, u["unit_type"], u.get("capacity_bpd"), _src(u.get("source_url"))),
                )

            # 5b. Slate — now carries commodity (defaults to 'crude') and run_id.
            for s in buf.get("slate", []):
                cur.execute(
                    """
                    INSERT INTO refineries.slate
                        (refinery_id, period_start, period_end, grade_name,
                         api_gravity, sulphur_pct, share_pct, commodity,
                         source_id, run_id)
                    VALUES (%s, %s::date, NULLIF(%s, '')::date, %s,
                            %s, %s, %s, %s,
                            %s, %s)
                    ON CONFLICT (refinery_id, period_start, grade_name) DO UPDATE SET
                        period_end  = EXCLUDED.period_end,
                        api_gravity = EXCLUDED.api_gravity,
                        sulphur_pct = EXCLUDED.sulphur_pct,
                        share_pct   = EXCLUDED.share_pct,
                        commodity   = EXCLUDED.commodity,
                        source_id   = EXCLUDED.source_id,
                        run_id      = EXCLUDED.run_id
                    """,
                    (
                        rid, s["period_start"], (s.get("period_end") or "").strip(),
                        s["grade_name"], s.get("api_gravity"), s.get("sulphur_pct"),
                        s.get("share_pct"), _norm_commodity(s.get("commodity")),
                        _src(s.get("source_url")), run_id,
                    ),
                )

            # 5c. Events.
            for e in buf.get("events", []):
                cur.execute(
                    """
                    INSERT INTO refineries.events
                        (refinery_id, event_type, start_date, end_date,
                         units_affected, capacity_impact_bpd, description, source_id)
                    VALUES (%s, %s,
                            NULLIF(%s, '')::date, NULLIF(%s, '')::date,
                            %s, %s, %s, %s)
                    """,
                    (
                        rid, e["event_type"],
                        (e.get("start_date") or "").strip(),
                        (e.get("end_date") or "").strip(),
                        e.get("units_affected"),
                        e.get("capacity_impact_bpd"),
                        e.get("description"),
                        _src(e.get("source_url")),
                    ),
                )

            # 5d. Financials.
            for f in buf.get("financials", []):
                cur.execute(
                    """
                    INSERT INTO refineries.financials
                        (refinery_id, period_start, period_end, period_type,
                         throughput_bpd, utilisation_pct,
                         revenue_usd_m, ebitda_usd_m, source_id)
                    VALUES (%s, %s::date, NULLIF(%s, '')::date, %s,
                            %s, %s, %s, %s, %s)
                    ON CONFLICT (refinery_id, period_start, period_type) DO UPDATE SET
                        throughput_bpd  = EXCLUDED.throughput_bpd,
                        utilisation_pct = EXCLUDED.utilisation_pct,
                        revenue_usd_m   = EXCLUDED.revenue_usd_m,
                        ebitda_usd_m    = EXCLUDED.ebitda_usd_m,
                        source_id       = EXCLUDED.source_id
                    """,
                    (
                        rid, f["period_start"], (f.get("period_end") or "").strip(),
                        f["period_type"], f.get("throughput_bpd"),
                        f.get("utilisation_pct"), f.get("revenue_usd_m"),
                        f.get("ebitda_usd_m"), _src(f.get("source_url")),
                    ),
                )

            # 5e. Consumption time-series — step-function (LOCF), one row per
            #     observation date, carries commodity + run_id so every data
            #     point traces back to its agent invocation.
            for m in buf.get("runs_obs", []):
                cur.execute(
                    """
                    INSERT INTO refineries.runs_ts
                        (refinery_id, observation_date, metric, value,
                         method, confidence, source_id, notes,
                         commodity, run_id)
                    VALUES (%s, %s::date, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (refinery_id, observation_date, metric) DO UPDATE SET
                        value      = EXCLUDED.value,
                        method     = EXCLUDED.method,
                        confidence = EXCLUDED.confidence,
                        source_id  = EXCLUDED.source_id,
                        notes      = EXCLUDED.notes,
                        commodity  = EXCLUDED.commodity,
                        run_id     = EXCLUDED.run_id
                    """,
                    (
                        rid, m["observation_date"], m["metric"], m.get("value"),
                        m.get("method"), m.get("confidence"),
                        _src(m.get("source_url")), m.get("notes"),
                        _norm_commodity(m.get("commodity")), run_id,
                    ),
                )

            # 6. Set primary_commodity on the refinery row now that the
            #    commodity exists in refineries.commodities.
            cur.execute(
                "UPDATE refineries.refinery SET primary_commodity = %s "
                "WHERE refinery_id = %s",
                (primary_commodity, rid),
            )

            # 7. Slate distribution time-series.
            #    First write any date-specific overrides the agent emitted,
            #    then derive remaining observation_dates from slate periods +
            #    fall back to primary_commodity. _derive_slate_distribution_ts
            #    is idempotent and override-aware: it skips (refinery, date)
            #    pairs that already have agent_override rows.
            for ms in buf.get("slate_obs", []):
                cur.execute(
                    """
                    INSERT INTO refineries.slate_distribution_ts
                        (refinery_id, observation_date, commodity, probability,
                         method, confidence, source_id, run_id, notes)
                    VALUES (%s, %s::date, %s, %s,
                            COALESCE(NULLIF(%s, ''), 'agent_override'),
                            %s, %s, %s, %s)
                    ON CONFLICT (refinery_id, observation_date, commodity) DO UPDATE SET
                        probability = EXCLUDED.probability,
                        method      = EXCLUDED.method,
                        confidence  = EXCLUDED.confidence,
                        source_id   = EXCLUDED.source_id,
                        run_id      = EXCLUDED.run_id,
                        notes       = EXCLUDED.notes
                    """,
                    (
                        rid, ms.get("observation_date"),
                        _norm_commodity(ms.get("commodity")),
                        ms.get("probability"),
                        (ms.get("method") or "").strip(),
                        ms.get("confidence"),
                        _src(ms.get("source_url")),
                        run_id, ms.get("notes"),
                    ),
                )

            _derive_slate_distribution_ts(
                rid, primary_commodity, run_id, cur,
            )

            # 8. Per-grade consumption decomposition — multiply aggregate
            #    throughput by the slate-distribution probability at the same
            #    observation_date to get per-commodity values. Preserves any
            #    'observed' rows from agent-emitted per-grade data.
            _derive_runs_by_grade_ts(rid, run_id, cur)

    # 7. Also write profile.json next to other agent outputs.
    out_dir = REFINERIES_OUT / rid
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "refinery": refinery,
        "result":   buf,
        "run_meta": {
            **meta,
            "run_id":      run_id,
            "started_at":  str(meta.get("started_at")),
            "finished_at": str(meta.get("finished_at")),
        },
    }
    (out_dir / "profile.json").write_text(json.dumps(profile, indent=2, default=str))
