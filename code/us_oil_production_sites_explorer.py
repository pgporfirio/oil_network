"""US Oil Production Sites Explorer — Claude Agent SDK agent.

For each major US oil-producing field, the agent:

  1. Web-searches and web-fetches EIA reports, state regulator portals (TX RRC,
     ND DMR, AK AOGCC, CO COGCC, NM OCD, BSEE), operator 10-Ks, Wikipedia,
     trade press.
  2. Records discovery/first-production years, current operator + WI-holder
     history, grades produced (with API and sulphur), reserves, annual and
     monthly production history, events (peaks, expansions, acquisitions),
     and logistics linkage (which pipelines/terminals move the crude).
  3. Persists everything to schema ``production_sites.*`` and drops a
     ``profile.json`` to ``outputs/production_sites/<field_id>/``.

There is also a **bootstrap** mode (``bootstrap_seed_async``) which runs the
agent once with a different prompt + tool set to enumerate the top ~500 US oil
fields. This populates ``production_sites.field`` with minimal identification
data; the per-field exploration loop then fills in everything else.

Authentication: relies on the Claude Code CLI subscription login
(``claude login``). No ``ANTHROPIC_API_KEY`` is required.

Usage from a notebook:

    import asyncio
    from us_oil_production_sites_explorer import (
        bootstrap_seed_async,
        list_fields,
        explore_field_async,
        persist_to_db,
        already_explored,
    )

    # One-off: build the seed list if empty.
    if not list_fields():
        await bootstrap_seed_async()

    # Per-field loop.
    for f in list_fields():
        if already_explored(f["field_id"]):
            continue
        buf, meta = await explore_field_async(f)
        persist_to_db(buf, f, meta)
"""
from __future__ import annotations

import asyncio
import json
import re
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

PRODUCTION_SITES_OUT = OUTPUTS_DIR / "production_sites"

_DB_KW = dict(
    host="localhost",
    dbname="eia_crude",
    user="eia_user",
    password="eia_password",
)


def _conn():
    return psycopg2.connect(**_DB_KW)


# --------------------------------------------------------------------------- #
# Desktop notification (Windows 11: system beep + toast; no-op elsewhere)     #
# --------------------------------------------------------------------------- #

def notify_done(summary: list[dict[str, Any]], mode: str = "run") -> None:
    """Fire a best-effort desktop notification after a batch of explorations.

    Always plays the Windows system 'OK' chime (stdlib only). Additionally
    tries to raise a Win11 toast via PowerShell + WinRT — silently no-ops on
    older Windows or non-Windows.
    """
    import sys
    n_done = sum(1 for s in summary if s.get("status") == "success")
    n_part = sum(1 for s in summary if s.get("status") == "partial")
    n_fail = sum(1 for s in summary if s.get("status") == "failed")
    cost   = sum((s.get("cost_usd") or 0) for s in summary)
    title  = f"Production sites explorer — {mode} done"
    body   = f"{n_done} ok, {n_part} partial, {n_fail} failed | cost ${cost:.2f}"

    if sys.platform != "win32":
        return

    # 1. Audible chime (stdlib).
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_OK)
    except Exception:
        pass

    # 2. Visible toast (PowerShell + WinRT). Failure is silent.
    import subprocess
    safe_title = title.replace('"', "'")
    safe_body  = body.replace('"', "'")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::"
        "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        f'$t.GetElementsByTagName("text")[0].AppendChild($t.CreateTextNode("{safe_title}")) | Out-Null; '
        f'$t.GetElementsByTagName("text")[1].AppendChild($t.CreateTextNode("{safe_body}")) | Out-Null; '
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        '"Production Sites Explorer").Show('
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Buffers — module-level mutable state, reset per exploration                 #
# --------------------------------------------------------------------------- #

# Per-field exploration buffer.
_BUFFER: dict[str, Any] = {}

# Bootstrap buffer (separate from per-field buffer to avoid collisions).
_SEED_BUFFER: dict[str, Any] = {}


def _reset_buffer(field_id: str) -> None:
    _BUFFER.clear()
    _BUFFER.update({
        "field_id":      field_id,
        "field_meta":    {},      # patches to production_sites.field
        "operators":     [],
        "grades":        [],
        "production":    [],
        "reserves":      [],
        "events":        [],
        "outages":       [],      # TAR + maintenance + hurricanes + curtailments
        "logistics":     [],
        "sources":       [],
        "agent_notes":   [],      # self-flagged caveats from the report_issue tool
        "summary":       None,
    })


def _reset_seed_buffer() -> None:
    _SEED_BUFFER.clear()
    _SEED_BUFFER.update({
        "fields":  [],
        "summary": None,
    })


def _slug(name: str, state: str | None = None) -> str:
    """Stable slug for field_id. e.g. ('Spraberry Trend', 'TX') -> 'spraberry_trend_tx'."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if state:
        return f"{base}_{state.lower()}"
    return base


# --------------------------------------------------------------------------- #
# Post-persist consistency scan — detect multi-run entries                    #
# --------------------------------------------------------------------------- #

# Each entry: (table_name, natural_key_sql, columns_for_row_payload).
# Tables not in this list either have a PK that already enforces uniqueness
# (`production_timeseries`) or are too noisy to scan (`field_outages` —
# different runs may legitimately record the same hurricane with slightly
# different dates).
_CONSISTENCY_TARGETS: list[tuple[str, str, list[str]]] = [
    ("field_operators",
     "lower(trim(coalesce(operator_name,''))) || '|' || coalesce(start_year::text,'')",
     ["operator_name", "parent_corp", "role", "share_pct", "start_year", "end_year"]),
    ("field_grades",
     "lower(trim(coalesce(grade_name,'')))",
     ["grade_name", "api_gravity", "sulphur_pct", "share_pct"]),
    ("reserves",
     "coalesce(as_of_date::text,'') || '|' || coalesce(category,'') || '|' || coalesce(commodity,'')",
     ["as_of_date", "category", "commodity", "volume", "unit"]),
    ("events",
     "coalesce(event_type,'') || '|' || coalesce(start_date::text,'')",
     ["event_type", "start_date", "end_date", "description"]),
    ("logistics_links",
     "coalesce(asset_type,'') || '|' || lower(trim(coalesce(asset_name,'')))",
     ["asset_type", "asset_name", "operator", "capacity_bpd", "direction"]),
    ("sources",
     "lower(trim(coalesce(url,'')))",
     ["url", "title", "publisher", "document_type"]),
]


def _scan_inconsistencies(cur, field_id: str) -> dict[str, list[dict[str, Any]]]:
    """For each child table, group rows by their natural key for this field
    and flag any key that has rows from more than one ``run_id``. Returns a
    dict keyed by table name; empty dict means no conflicts found.
    """
    findings: dict[str, list[dict[str, Any]]] = {}
    for table_name, key_sql, payload_cols in _CONSISTENCY_TARGETS:
        payload_csv = ", ".join(payload_cols)
        cur.execute(
            f"""
            WITH labelled AS (
                SELECT
                    ({key_sql}) AS natural_key,
                    run_id,
                    {payload_csv}
                FROM production_sites.{table_name}
                WHERE field_id = %s
                  AND run_id IS NOT NULL
            ),
            multi AS (
                SELECT natural_key
                FROM labelled
                GROUP BY natural_key
                HAVING COUNT(DISTINCT run_id) > 1
            )
            SELECT l.natural_key,
                   array_agg(DISTINCT l.run_id ORDER BY l.run_id) AS run_ids,
                   jsonb_agg(jsonb_build_object(
                        'run_id',  l.run_id,
                        {",".join(f"'{c}', l.{c}" for c in payload_cols)}
                   )) AS rows
            FROM labelled l
            JOIN multi USING (natural_key)
            GROUP BY l.natural_key
            ORDER BY l.natural_key
            """,
            (field_id,),
        )
        conflicts = []
        for natural_key, run_ids, rows in cur.fetchall():
            if not natural_key:    # ignore empty keys
                continue
            conflicts.append({
                "natural_key": natural_key,
                "run_ids":     list(run_ids),
                "rows":        rows,
            })
        if conflicts:
            findings[table_name] = conflicts
    return findings


# Production observations are sparse step-function rows — value at
# observation_date carries forward until the next observation overrides
# it. The agent emits one row per change point (annual baseline, outage
# start, restore-to-prior-value, peak, restart), not one row per month.
# Rate metrics only — volume metrics would break the step-function
# interpretation and are excluded from the agent's allowed set.
_ALLOWED_PRODUCTION_METRICS: set[str] = {"oil_bpd", "gas_mcfd", "water_bpd", "wells_count"}


# --------------------------------------------------------------------------- #
# Tools — registered with the Agent SDK via @tool + create_sdk_mcp_server     #
# --------------------------------------------------------------------------- #

# ---- Bootstrap tools ------------------------------------------------------- #

@tool(
    "seed_field",
    "Register one US oil-producing field for the seed list. Provide as much "
    "identifying info as you have at this stage — the per-field exploration "
    "pass will fill in everything else. state is the 2-letter USPS code "
    "(or 'GoM' for federal Gulf of Mexico waters). basin is one of: Permian, "
    "Bakken, Eagle Ford, DJ/Niobrara, Anadarko/SCOOP/STACK, Powder River, "
    "Uinta, Williston, San Joaquin, Los Angeles, Alaska North Slope, "
    "Alaska Cook Inlet, Gulf of Mexico, Appalachian, etc. field_type: "
    "'conventional'|'tight-oil'|'shale'|'heavy-oil'|'offshore-deepwater'|"
    "'offshore-shelf'|'arctic'.",
    {
        "name":             str,
        "state":            str,
        "basin":            str,
        "field_type":       str,
        "play":             str,
        "onshore_offshore": str,
        "current_operator": str,
        "rank_notes":       str,   # free-form: rank, est. production, etc.
    },
)
async def seed_field(args: dict[str, Any]) -> dict[str, Any]:
    _SEED_BUFFER["fields"].append(args)
    return {"content": [{"type": "text", "text": f"OK: seeded {args['name']}"}]}


@tool(
    "finalise_seed",
    "Call once at the end of the bootstrap pass with a 2-3 sentence summary "
    "describing how many fields were enumerated, which sources were used, and "
    "what coverage gaps remain (e.g. private operators, very small fields).",
    {"summary": str},
)
async def finalise_seed(args: dict[str, Any]) -> dict[str, Any]:
    _SEED_BUFFER["summary"] = args["summary"]
    return {"content": [{"type": "text", "text": "seed finalised."}]}


_seed_server = create_sdk_mcp_server(
    name="psexp_seed",
    version="1.0.0",
    tools=[seed_field, finalise_seed],
)


# ---- Per-field exploration tools ------------------------------------------- #

@tool(
    "query_eia_production",
    "Query monthly/STEO crude-production timeseries from the local oil_network DB. "
    "Match by asset_id (preferred) or by a name fragment. "
    "Valid asset_id values include: permian, permian_nm, bakken, bakken_nd, "
    "eagle_ford_tx, alaska_north_slope, gulf_of_america, california_conventional, "
    "colorado_conventional, oklahoma_conventional, wyoming_conventional, "
    "montana_state_view, texas_state_view, usa_view, padd1_view..padd5_view, "
    "usa_lower48_excl_gom_view. "
    "name_filter is an ILIKE fragment on the series name "
    "(e.g. 'Permian basin', 'crude production', 'STEO'). "
    "since_date is ISO YYYY-MM-DD (default 2018-01-01). "
    "Returns latest-vintage rows grouped by series, up to 1200 rows total.",
    {"asset_id": str, "name_filter": str, "since_date": str},
)
async def query_eia_production(args: dict[str, Any]) -> dict[str, Any]:
    asset_id = (args.get("asset_id") or "").strip()
    name_filter = (args.get("name_filter") or "").strip()
    since = (args.get("since_date") or "").strip() or "2018-01-01"
    rows: list[dict[str, Any]] = []
    series_meta: list[dict[str, Any]] = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (td.timeseries_id, td.observation_date)
                       td.timeseries_id, td.observation_date, td.value,
                       t.name, t.unit, t.asset_id
                FROM oil_network.timeseries_data td
                JOIN oil_network.timeseries t USING (timeseries_id)
                WHERE t.source = 'eia'
                  AND t.timeseries_type = 'production'
                  AND (%s = '' OR t.asset_id = %s)
                  AND t.name ILIKE %s
                  AND td.observation_date >= %s::date
                ORDER BY td.timeseries_id, td.observation_date, td.saved_date DESC
                LIMIT 1200
                """,
                (asset_id, asset_id, f"%{name_filter}%", since),
            )
            seen: dict[str, dict[str, Any]] = {}
            for ts_id, obs, val, nm, unit, aid in cur.fetchall():
                rows.append({
                    "timeseries_id": ts_id,
                    "date": str(obs),
                    "value": float(val) if val is not None else None,
                })
                if ts_id not in seen:
                    seen[ts_id] = {
                        "timeseries_id": ts_id,
                        "name": nm, "unit": unit, "asset_id": aid, "count": 0,
                    }
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
    "update_field_meta",
    "Update master-row attributes for the field. Call this once you have a "
    "consolidated view. discovery_year and first_production_year are "
    "4-digit years; status is 'active'|'declining'|'shut-in'|'abandoned'; "
    "field_type is 'conventional'|'tight-oil'|'shale'|'heavy-oil'|"
    "'offshore-deepwater'|'offshore-shelf'|'arctic'. lat/lon are decimal "
    "degrees (positive N, negative W for US). aliases is a comma-separated "
    "list of alternate names.",
    {
        "aliases":               str,
        "basin":                 str,
        "play":                  str,
        "state":                 str,
        "onshore_offshore":      str,
        "latitude":              float,
        "longitude":             float,
        "discovery_year":        int,
        "first_production_year": int,
        "status":                str,
        "field_type":            str,
        "notes":                 str,
    },
)
async def update_field_meta(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["field_meta"].update({k: v for k, v in args.items() if v not in (None, "", 0)})
    return {"content": [{"type": "text", "text": "OK: meta updated"}]}


@tool(
    "record_operator",
    "Record an operator, working-interest holder, or royalty holder. role: "
    "'operator'|'wi_holder'|'royalty'|'former_operator'. share_pct is the "
    "ownership share if disclosed. start_year/end_year bracket the operatorship "
    "(end_year=0 means current). parent_corp is the publicly traded parent if "
    "different from operator_name.",
    {
        "operator_name": str,
        "parent_corp":   str,
        "role":          str,
        "share_pct":     float,
        "start_year":    int,
        "end_year":      int,
        "source_url":    str,
    },
)
async def record_operator(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["operators"].append(args)
    return {"content": [{"type": "text", "text": f"OK: operator {args['operator_name']}"}]}


@tool(
    "record_grade",
    "Record a crude grade produced from this field. grade_name is the marketed "
    "name when available (e.g. 'WTI Midland', 'Bakken Light', 'ANS', 'Mars "
    "Blend', 'LLS'); use heavy/medium/light if no named grade exists. "
    "api_gravity in degrees API. sulphur_pct in weight percent. share_pct is "
    "the field's output share for this grade. period_start/end are ISO dates "
    "(YYYY-MM-DD); leave empty for current.",
    {
        "grade_name":   str,
        "api_gravity":  float,
        "sulphur_pct":  float,
        "share_pct":    float,
        "period_start": str,
        "period_end":   str,
        "source_url":   str,
        "notes":        str,
    },
)
async def record_grade(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["grades"].append(args)
    return {"content": [{"type": "text", "text": f"OK: grade {args['grade_name']}"}]}


@tool(
    "record_production",
    "Record one production observation. The DB stores production as a SPARSE "
    "step-function time series — the value at observation_date is the rate "
    "from that date forward until the next observation overrides it. Emit "
    "one row per *change point*, NOT one row per month. \n\n"
    "STEP-FUNCTION SEMANTIC by example: if 2018 averaged 320 kbpd (320,000 "
    "bpd), emit ONE row at 2018-01-01, value=320000, method='annual_baseline'. "
    "If 2019 averaged 410 kbpd, emit ONE row at 2019-01-01, value=410000. "
    "If a hurricane took the field offline from 2021-08-29 to 2021-11-05, "
    "emit (2021-08-29, 0, 'outage_event') and (2021-11-05, <restore-value>, "
    "'restore'). The consumer interpolates everything in between as the "
    "carried-forward value. \n\n"
    "observation_date: YYYY-MM-DD (any date — typically Jan 1 for annual "
    "baselines, the exact outage/restart day for events, peak month for "
    "milestones).\n"
    "metric: 'oil_bpd' (oil production in barrels-per-day — the primary "
    "metric), 'gas_mcfd' (associated gas, thousand-cubic-feet-per-day), "
    "'water_bpd' (water production), 'wells_count' (count of producing "
    "wells). Volume metrics are NOT allowed — they don't carry forward as a "
    "step function. If a source quotes annual barrels (e.g. '180 MMbbl in "
    "2020'), convert to bpd before emitting: 180e6 / 365 = 493,150 bpd.\n"
    "method: 'state_reg' (per-month regulator data, dense), 'eia_attributed' "
    "(EIA basin/state aggregate attributed to the field by share), "
    "'operator_10K' (disclosed annual figure converted to bpd), "
    "'annual_baseline' (annual average rate posted at Jan 1 of the year), "
    "'outage_event' (zero or reduced rate at the outage start date), "
    "'restore' (rate restored at outage end), 'peak' (peak-month rate), "
    "'estimated' (triangulated). \n"
    "confidence: 'high' (directly disclosed at the chosen grain), 'medium' "
    "(one transformation, e.g. annual→bpd), 'low' (multiple imputations, "
    "e.g. district-level data attributed by capacity share).",
    {
        "observation_date": str,
        "metric":           str,
        "value":            float,
        "method":           str,
        "confidence":       str,
        "source_url":       str,
        "notes":            str,
    },
)
async def record_production(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["production"].append(args)
    return {"content": [{"type": "text", "text": f"OK: {args['metric']} {args.get('observation_date', '?')} = {args.get('value')}"}]}


@tool(
    "record_reserves",
    "Record a reserve estimate. category: 'proved'|'probable'|'possible'|'2P'|"
    "'3P'|'OOIP' (original oil in place). commodity: 'oil'|'gas'|'ngl'. unit: "
    "'mmbbl'|'bcf'|'mmboe'.",
    {
        "as_of_date": str,
        "category":   str,
        "commodity":  str,
        "volume":     float,
        "unit":       str,
        "source_url": str,
        "notes":      str,
    },
)
async def record_reserves(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["reserves"].append(args)
    return {"content": [{"type": "text", "text": f"OK: reserves {args['category']} {args['volume']} {args['unit']}"}]}


@tool(
    "record_event",
    "Record an event: discovery, first_production, peak, expansion, "
    "acquisition, divestiture, shutdown, fire, spill. Dates ISO (YYYY-MM-DD); "
    "leave end_date empty if it's a single-point event.",
    {
        "event_type":  str,
        "start_date":  str,
        "end_date":    str,
        "description": str,
        "source_url":  str,
    },
)
async def record_event(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["events"].append(args)
    return {"content": [{"type": "text", "text": f"OK: event {args['event_type']}"}]}


@tool(
    "record_outage",
    "Record a historical outage period — turnaround, planned maintenance, "
    "hurricane, freeze-off, OPEC-style curtailment, pipeline takeaway "
    "disruption, fire, spill, or shut-in. Use this for time-windowed "
    "production suppression. start_date and end_date are ISO YYYY-MM-DD; "
    "leave end_date empty if the outage is still ongoing or the end is "
    "unknown. outage_type: 'TAR' (turnaround / scheduled maintenance), "
    "'unplanned_maintenance', 'hurricane', 'storm', 'curtailment' (OPEC / "
    "regulatory / voluntary cut), 'pipeline' (takeaway constrained), "
    "'fire', 'spill', 'freeze' (winter freeze-off, e.g. Bakken / Permian), "
    "'power', 'shutdown' (any other), 'other'. impact_pct is the production "
    "reduction percentage 0-100 — pass 100 (or leave 0) for a full "
    "shutdown, lower values for partial curtailments. description is a "
    "1-2 sentence summary (e.g. 'Hurricane Ida — full GoM offshore "
    "evacuation 26 Aug-2 Sep 2021').\n\n"
    "WHY THIS MATTERS: when you record an annual production figure and the "
    "DB splits it evenly across 12 months, that hides any real-world "
    "outages. After listing the outages here, REVISIT your annual_split "
    "production calls — for years where outages covered N months, emit "
    "the annual total spread across only the (12 - N) operating months "
    "(period_start/period_end excluding the outage range) and emit "
    "ZERO-value monthly rows (or partial values when impact_pct < 100) for "
    "the outage months. This gives a more realistic monthly time series.",
    {
        "start_date":  str,
        "end_date":    str,
        "outage_type": str,
        "impact_pct":  float,
        "description": str,
        "source_url":  str,
    },
)
async def record_outage(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["outages"].append(args)
    return {"content": [{"type": "text", "text": f"OK: outage {args.get('outage_type')} {args.get('start_date')}..{args.get('end_date') or 'open'}"}]}


@tool(
    "record_logistics",
    "Record a logistics linkage — which pipeline, terminal, rail facility, or "
    "gathering system moves crude from this field. asset_type: 'pipeline'|"
    "'terminal'|'rail_terminal'|'gathering_system'|'refinery'. direction is "
    "'outbound' from the field's perspective (default). capacity_bpd is the "
    "asset's nameplate or contracted capacity if disclosed.",
    {
        "asset_type":   str,
        "asset_name":   str,
        "operator":     str,
        "capacity_bpd": int,
        "direction":    str,
        "notes":        str,
        "source_url":   str,
    },
)
async def record_logistics(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["logistics"].append(args)
    return {"content": [{"type": "text", "text": f"OK: logistics {args['asset_name']}"}]}


@tool(
    "record_source",
    "Register a source URL the agent consulted. document_type: 'eia'|"
    "'state_regulator'|'10-K'|'press_release'|'news'|'wikipedia'|"
    "'investor_presentation'|'trade_press'|'sustainability_report'|'other'. "
    "published_date is YYYY-MM-DD if available, else empty.",
    {
        "url":            str,
        "title":          str,
        "publisher":      str,
        "document_type":  str,
        "published_date": str,
        "notes":          str,
    },
)
async def record_source(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["sources"].append(args)
    return {"content": [{"type": "text", "text": f"OK: source {args['url'][:60]}"}]}


@tool(
    "report_issue",
    "Self-flag a concern about this run for downstream human review. Use "
    "for caveats the structured emit tools don't capture: data-quality "
    "doubts ('Devon's Spraberry WI position unconfirmed'), source-access "
    "failures ('TX RRC PDF returned 403; field-level data not retrieved'), "
    "research dead-ends ('Sinochem 2013 disposition unknown'), or any "
    "judgement call you want flagged. Many small calls are fine — these "
    "land in exploration_runs.agent_notes as a JSON list.\n"
    "type: 'data_gap' (couldn't find something) | 'source_inaccessible' "
    "(blocked) | 'low_confidence' (figure is triangulated/estimated) | "
    "'inconsistent_sources' (sources disagreed) | 'methodology' (note about "
    "an attribution choice) | 'other'.\n"
    "severity: 'high' (this materially affects the field profile) | 'medium' "
    "(useful to flag) | 'low' (FYI).",
    {
        "type":        str,
        "severity":    str,
        "description": str,
    },
)
async def report_issue(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER.setdefault("agent_notes", []).append({
        "type":        args.get("type") or "other",
        "severity":    args.get("severity") or "medium",
        "description": args.get("description") or "",
    })
    return {"content": [{"type": "text", "text": f"OK: issue [{args.get('severity')}] {args.get('type')}"}]}


@tool(
    "finalise",
    "Call exactly once at the end. Provide a 2-3 sentence summary covering "
    "what was found, key uncertainties, and any data gaps that future passes "
    "should address.",
    {"summary": str},
)
async def finalise(args: dict[str, Any]) -> dict[str, Any]:
    _BUFFER["summary"] = args["summary"]
    return {"content": [{"type": "text", "text": "finalised."}]}


_explorer_server = create_sdk_mcp_server(
    name="psexp",
    version="1.0.0",
    tools=[
        query_eia_production,
        update_field_meta,
        record_operator,
        record_grade,
        record_production,
        record_reserves,
        record_event,
        record_outage,
        record_logistics,
        record_source,
        report_issue,
        finalise,
    ],
)


# --------------------------------------------------------------------------- #
# Prompts                                                                     #
# --------------------------------------------------------------------------- #

SEED_SYSTEM_PROMPT = """You are a petroleum-geology research analyst building a comprehensive list of the major US oil-producing fields.

You have:
- WebSearch — Google-style web search.
- WebFetch — fetch a URL.
- seed_field — register one field. Call MANY times (target ~400-500 fields).
- finalise_seed — call once at the end with a summary.

YOUR GOAL — enumerate the top US oil-producing fields, covering as comprehensively as you can from public sources:

1. EIA "U.S. Crude Oil and Natural Gas Proved Reserves" reports and the older EIA "Top 100 Oil and Gas Fields" report.
2. State-regulator field rosters: Texas RRC (oil & gas field master list), North Dakota DMR (Bakken field list), Alaska AOGCC, Colorado COGCC (DJ/Niobrara), New Mexico OCD (Delaware Basin), California DOGGR / CalGEM (San Joaquin, LA basin), Wyoming WOGCC (Powder River), Oklahoma OCC (Anadarko/SCOOP/STACK), Utah DOGM (Uinta).
3. BSEE for federal offshore Gulf of Mexico fields, BOEM for Pacific offshore (very few).
4. Operator 10-Ks of major US E&Ps (ExxonMobil, Chevron, ConocoPhillips, Occidental, Pioneer/EXOM combined, Devon, Diamondback, EOG, Hess, Marathon Oil, Continental, ContinentalResources, APA, Coterre, etc.) — they list their key producing properties.
5. Wikipedia "List of oil fields" articles per basin.

COVERAGE TARGETS — try to enumerate roughly:
- ~80-120 Permian fields/units (Spraberry, Wolfcamp, Bone Spring, Avalon, Yeso, etc.)
- ~30-50 Bakken / Three Forks fields
- ~30-40 Eagle Ford fields
- ~20-30 DJ / Niobrara fields
- ~20-30 SCOOP / STACK / Anadarko fields
- ~20-30 Powder River / Uinta / Williston conventional
- ~15-25 San Joaquin / LA-basin California heavy-oil fields
- ~30-40 Alaska North Slope + Cook Inlet fields (Prudhoe Bay, Kuparuk, Alpine, etc.)
- ~30-50 GoM federal-offshore fields (Mars, Atlantis, Thunder Horse, Tahiti, Auger, Mad Dog, etc.)
- ~20-30 Appalachian + miscellaneous

WORKING PROCEDURE:
1. Start with WebSearch for "EIA top oil fields United States" and the per-state regulator field lists.
2. WebFetch the most authoritative roster you find for each basin.
3. Call seed_field for EACH field — include name, state, basin, field_type, play (sub-formation), onshore/offshore, current_operator (best known), rank_notes (any free-form info like est. production, peak year, etc.).
4. Be GENEROUS but not duplicative. Use the marketed field name; record common aliases in rank_notes.
5. finalise_seed when done — describe coverage and the largest gaps.

RULES:
- BRITISH SPELLING in any natural-language strings ("modelled", "behaviour", "colour", "organisation").
- Don't fabricate. If a field's basin or operator is uncertain, write "unknown" and note it in rank_notes.
- It's OK to be incomplete — better to enumerate 300 fields well than to invent 500 poorly.
- TARGET ~50-100 tool calls total (seed_field + a handful of WebFetch).
"""


SEED_USER_PROMPT = """Build the seed list of US oil-producing fields.

Cover all the major US producing basins. Aim for ~300-500 fields. Use EIA reports, state regulator rosters, BSEE for offshore, and operator 10-Ks. Cite sources in your finalise_seed summary."""


SYSTEM_PROMPT = """You are a petroleum-geology research analyst. For one US oil-producing field, you build a comprehensive profile from public sources.

You have:
- WebSearch — Google-style web search.
- WebFetch — fetch a URL (HTML or PDF).
- query_eia_production — read monthly crude-production timeseries from the local DB. EIA production data is state- and basin-aggregated, NOT per-field. The local DB has 20 production series — typical asset_id values: 'permian', 'permian_nm', 'bakken', 'bakken_nd', 'eagle_ford_tx', 'alaska_north_slope', 'gulf_of_america', 'california_conventional', 'colorado_conventional', 'oklahoma_conventional', 'wyoming_conventional', 'texas_state_view', 'usa_view'. ONE call by asset_id is usually enough; do NOT WebFetch EIA pages when the local DB returns rows.
- update_field_meta — patch the master-row attributes (basin, play, lat/lon, discovery year, status, type).
- record_operator, record_grade, record_production, record_reserves, record_event, record_outage, record_logistics, record_source — structured emit. Call many small records; they are buffered and persisted at the end.
- report_issue — self-flag a caveat the structured tools don't capture (data gap, source 403, low-confidence figure, conflicting sources). These land in exploration_runs.agent_notes for human review. Use liberally — better to over-flag than to silently let issues through.
- finalise — call exactly once at the end with a 2-3 sentence summary.

YOUR GOAL — fill in, to the best of your ability:

1. **Field-level metadata.** Basin, play / sub-formation, lat/lon (decimal degrees), discovery year, first-production year, current status, field type (tight-oil, shale, conventional, heavy-oil, offshore-deepwater, offshore-shelf, arctic). Use update_field_meta.

2. **Operators and ownership.** Current operator and parent corporation. Working-interest holders. Material ownership changes in the past 10-15 years (acquisitions, divestitures, MLP carve-outs). Use record_operator.

3. **Grades produced.** Marketed name (WTI Midland, Bakken Light, ANS, Mars Blend, LLS, Eagle Ford 47°, etc.), API gravity, sulphur weight-percent, share of field output. Use record_grade.

4. **Production history (SPARSE STEP-FUNCTION time-series).** The DB stores production rates as a *sparse* step-function — one row per (field, observation_date, metric), with value in **bpd** (barrels-per-day). The value at observation_date is the prevailing rate FROM that date FORWARD, until the next observation overrides it. **Emit one row per change point, NOT one row per month.** This matches `oil_network.timeseries_data` and is the canonical shape across the project.

   Examples of correct emission:
   - **Annual disclosed**: "Pioneer Spraberry averaged 320 kbpd in 2018, 410 kbpd in 2019, 380 kbpd in 2020" → emit THREE rows:
     - (2018-01-01, 320000, method='annual_baseline')
     - (2019-01-01, 410000, method='annual_baseline')
     - (2020-01-01, 380000, method='annual_baseline')
     Each value carries forward through the year until the next observation.
   - **Monthly disclosed** (TX RRC, ND DMR — Bakken has clean monthly field-level data): emit one row per month at the YYYY-MM-01 date with the actual monthly bpd. Use method='state_reg'.
   - **Cumulative figure**: "180 MMbbl produced in 2020" → convert to average bpd (180e6 / 365 ≈ 493,150) and emit ONE row at 2020-01-01.
   - **EIA basin/state aggregate attributed by share**: emit observations at the same dates as the EIA series (typically monthly), but apply your judgement share to the value. method='eia_attributed', confidence='low', noted in `notes`.

   metric: 'oil_bpd' (primary), 'gas_mcfd' (associated gas), 'water_bpd', 'wells_count'. Always convert sources to bpd / mcfd / count before emitting. Volume metrics (oil_bbl_total) are NOT allowed — they don't have a step-function interpretation.

5. **Reserves.** Proved (1P), 2P, 3P, OOIP if disclosed. From operator 10-Ks (SEC requires proved reserves disclosure), USGS field assessments, EIA estimates. Use record_reserves.

6. **Events.** Discovery, first production, peak year, expansions, acquisitions/divestitures, shutdowns, major fires/spills. Use record_event for *point-in-time* markers (or short events with a known calendar date).

7. **Outages (TAR + maintenance + hurricanes + curtailments).** Time-windowed periods when production was suppressed. Search for: turnaround (TAR) / scheduled-maintenance announcements in operator press releases, hurricane evacuations (BSEE issues "Tropical Activity Statistical" tables for the GoM), winter freeze-offs (Bakken Jan 2014, Permian Feb 2021 Uri), OPEC-style voluntary curtailments, pipeline takeaway disruptions, fires, spills. For each outage call record_outage with start_date, end_date, outage_type, impact_pct (0-100 reduction), description, source_url. **This is distinct from record_event** — events are point markers, outages are intervals. The agent should target ~3-10 outage records per active field; less for shut-in fields.

8. **Outage-aware emission (use the step-function shape).** After recording outages via `record_outage`, integrate them INTO the production_timeseries series by emitting paired (outage_event, restore) observations. The step-function semantic makes this very economical:

   - **Full outage** (impact_pct = 100): emit `(outage_start_date, 0, method='outage_event')` then `(outage_end_date, <prior_rate>, method='restore')`. The zero value carries forward through the outage; the restore value carries forward afterward until the next annual_baseline supersedes it.
   - **Partial curtailment** (impact_pct < 100): emit `(outage_start_date, <prior_rate × (1 - impact_pct/100)>, method='outage_event')` then `(outage_end_date, <prior_rate>, method='restore')`. E.g. a 30% curtailment on a 400-kbpd field: emit (start_date, 280000, …) and (end_date, 400000, …).
   - **Annual-baseline observations are unaffected** — you don't need to revisit them. The (outage_event, restore) pair sits between consecutive annual baselines and the step-function carries the right value through each phase.

   Example for Prudhoe Bay 2006 corrosion shutdown (Aug-Dec, 50%):
     (2006-01-01, 800000, method='annual_baseline')      ← baseline carries forward to 2006-08-07
     (2006-08-07, 400000, method='outage_event')         ← 50% impact starts
     (2006-12-01, 800000, method='restore')              ← back to baseline
     (2007-01-01, 750000, method='annual_baseline')      ← new annual baseline takes over

   The goal: the consumer can replay the step function and see exactly when production was suppressed without storing 12 monthly rows per year.

9. **Logistics linkage.** Which pipelines, gathering systems, terminals, rail-loading facilities take the crude away from this field? Match to named pipelines (e.g. 'Gray Oak', 'EPIC', 'Cactus II', 'Dakota Access', 'TAPS') and terminals (Cushing, Magellan East Houston, Patoka, etc.). Use record_logistics.

WORKING PROCEDURE:

1. Start with WebSearch for the field name + "field" + state. Wikipedia, EIA reports, USGS field assessments, operator 10-Ks all surface quickly.
2. For shale plays: search state regulator portal — TX RRC for Permian/Eagle Ford, ND DMR for Bakken, CO COGCC for DJ. These have authoritative field-level monthly data.
3. For offshore GoM: BSEE field master + operator 10-K. Production data is BSEE's "OGOR" / monthly production reports.
4. For Alaska: AOGCC for individual fields; the operator's 10-K (ConocoPhillips, Hilcorp, ExxonMobil) tends to disclose Prudhoe Bay-level data.
5. Always **record_source** every URL you consult — even ones that didn't yield data, with notes='no relevant data'.
6. Use **update_field_meta** once you have a consolidated view of the master attributes.
7. **finalise** at the end with a 2-3 sentence summary describing what was found, what's uncertain, and what gaps remain.

RULES:

- BRITISH SPELLING in any natural-language strings ("modelled", "utilisation", "behaviour", "colour", "organisation").
- BE SCEPTICAL. If two sources conflict, record both with their source URLs. Don't fabricate.
- GAPS ARE VALUABLE. In the finalise summary, explicitly state what you couldn't find.
- Each record_* call should carry a source_url where possible. If the fact is triangulated, pick the most authoritative URL used.
- TARGET ~30-50 tool calls. Don't dwell on any single source for more than a few fetches.
"""


USER_PROMPT_TEMPLATE = """Research this US oil-producing field and build its profile.

Field ID: {field_id}
Field name: {name}
State: {state}
Basin: {basin}
Play: {play}
Field type: {field_type}
Onshore/Offshore: {onshore_offshore}
Initial operator hypothesis: {current_operator}
Seed notes: {rank_notes}

Build the profile. Cover: field metadata (lat/lon, discovery year, status, type), operator + ownership history, grades produced with API and sulphur, annual + monthly production history (state regulator data preferred), reserves, events (discovery, peak, expansions, acquisitions), and logistics linkage (pipelines and terminals that move the crude). Save every source via record_source. End with finalise.
"""


# --------------------------------------------------------------------------- #
# Loading fields from the DB                                                  #
# --------------------------------------------------------------------------- #

def list_fields() -> list[dict[str, Any]]:
    """Return all seeded fields, ordered by basin then name."""
    sql = """
        SELECT field_id, name, state, basin, play, field_type,
               onshore_offshore, notes, last_explored_at
        FROM production_sites.field
        ORDER BY basin NULLS LAST, state NULLS LAST, name
    """
    out: list[dict[str, Any]] = []
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            out.append({
                "field_id":         row[0],
                "name":             row[1],
                "state":            row[2],
                "basin":            row[3],
                "play":             row[4],
                "field_type":       row[5],
                "onshore_offshore": row[6],
                "rank_notes":       row[7],
                "last_explored_at": row[8],
                "current_operator": None,   # filled by exploration loop, not seed
            })
    return out


def already_explored(field_id: str) -> bool:
    """True if a successful exploration_runs row exists for this field."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM production_sites.exploration_runs "
            "WHERE field_id = %s AND status = 'success' LIMIT 1",
            (field_id,),
        )
        return cur.fetchone() is not None


# --------------------------------------------------------------------------- #
# Agent invocation — bootstrap seed list                                      #
# --------------------------------------------------------------------------- #

async def bootstrap_seed_async(
    model: str = "claude-sonnet-4-6",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the bootstrap pass: enumerate the top US oil fields.

    Writes one row per field into ``production_sites.field`` (minimal info —
    name, state, basin, play, field_type, onshore_offshore, current_operator
    saved into notes as 'seed_op:<name>'). Also writes a seed_list.json blob
    to outputs/production_sites/seed_list.json.

    Returns (seed_buffer, run_meta).
    """
    _reset_seed_buffer()

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=SEED_SYSTEM_PROMPT,
        mcp_servers={"psexp_seed": _seed_server},
        allowed_tools=[
            "WebSearch", "WebFetch",
            "mcp__psexp_seed__seed_field",
            "mcp__psexp_seed__finalise_seed",
        ],
        permission_mode="bypassPermissions",
    )

    started = datetime.utcnow()
    tool_calls = 0
    tokens_in = tokens_out = 0
    cost_usd = 0.0
    error: str | None = None
    try:
        async for message in query(prompt=SEED_USER_PROMPT, options=options):
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
        "status":      "failed" if error else ("partial" if _SEED_BUFFER["summary"] is None else "success"),
        "error":       error,
    }

    # Persist seed list to JSON.
    PRODUCTION_SITES_OUT.mkdir(parents=True, exist_ok=True)
    seed_path = PRODUCTION_SITES_OUT / "seed_list.json"
    seed_path.write_text(json.dumps({
        "summary":  _SEED_BUFFER["summary"],
        "run_meta": {**meta, "started_at": str(meta["started_at"]),
                              "finished_at": str(meta["finished_at"])},
        "fields":   _SEED_BUFFER["fields"],
    }, indent=2, default=str))

    # Persist to production_sites.field (idempotent — ON CONFLICT DO NOTHING).
    _insert_seed_fields(_SEED_BUFFER["fields"])

    return dict(_SEED_BUFFER), meta


def _insert_seed_fields(fields: list[dict[str, Any]]) -> None:
    """Insert one row per seeded field. Idempotent on field_id."""
    if not fields:
        return
    rows = []
    seen_ids: set[str] = set()
    for f in fields:
        name = (f.get("name") or "").strip()
        state = (f.get("state") or "").strip().upper()
        if not name:
            continue
        fid = (f.get("id") or f.get("field_id") or "").strip() or _slug(name, state if state else None)
        if fid in seen_ids:
            continue
        seen_ids.add(fid)
        op = (f.get("current_operator") or "").strip()
        notes = f.get("rank_notes") or ""
        if op:
            notes = (notes + f"\nseed_op:{op}").strip()
        rows.append((
            fid,
            name,
            f.get("basin") or None,
            f.get("play") or None,
            state or None,
            f.get("onshore_offshore") or None,
            f.get("field_type") or None,
            notes or None,
        ))

    with _conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO production_sites.field
                (field_id, name, basin, play, state, onshore_offshore,
                 field_type, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (field_id) DO NOTHING
            """,
            rows,
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# Deterministic seed — load from JSON (alternative to bootstrap_seed_async)   #
# --------------------------------------------------------------------------- #

def load_seed_from_json(
    json_path: str | Path | None = None,
) -> dict[str, Any]:
    """Populate ``production_sites.field`` from a curated JSON seed file.

    Replaces the agent-driven ``bootstrap_seed_async`` for cases where the
    seed list is hand-curated from authoritative public rosters (EIA, state
    regulators, BSEE). Mirrors how Stage 1 loaded its asset graph from
    ``config/asset_graph.json``.

    Each entry in the JSON ``fields`` array must include at minimum
    ``id`` (or ``field_id``), ``name``, ``state``; optionally ``basin``,
    ``play``, ``field_type``, ``onshore_offshore``, ``current_operator``,
    ``rank_notes``. Insert is idempotent on field_id.

    Default path: ``<project>/Stage2/config/production_sites_seed.json``.

    Returns a small summary dict with counts.
    """
    if json_path is None:
        from paths import CODE_DIR  # local import keeps module import-clean
        json_path = CODE_DIR.parent / "config" / "production_sites_seed.json"
    json_path = Path(json_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    entries = payload.get("fields", [])

    before = len(list_fields())
    _insert_seed_fields(entries)
    after = len(list_fields())

    return {
        "json_path":   str(json_path),
        "entries":     len(entries),
        "before":      before,
        "after":       after,
        "inserted":    after - before,
        "meta":        payload.get("meta", {}),
    }


# --------------------------------------------------------------------------- #
# Agent invocation — per-field exploration                                    #
# --------------------------------------------------------------------------- #

async def explore_field_async(
    field: dict[str, Any],
    model: str = "claude-sonnet-4-6",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the agent on one field. Returns (buffer, run_meta)."""
    _reset_buffer(field["field_id"])

    user_prompt = USER_PROMPT_TEMPLATE.format(
        field_id=          field["field_id"],
        name=              field.get("name") or "",
        state=             field.get("state") or "",
        basin=             field.get("basin") or "(unknown)",
        play=              field.get("play") or "(unknown)",
        field_type=        field.get("field_type") or "(unknown)",
        onshore_offshore=  field.get("onshore_offshore") or "(unknown)",
        current_operator=  field.get("current_operator") or "(unknown)",
        rank_notes=        field.get("rank_notes") or "",
    )

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"psexp": _explorer_server},
        allowed_tools=[
            "WebSearch", "WebFetch",
            "mcp__psexp__query_eia_production",
            "mcp__psexp__update_field_meta",
            "mcp__psexp__record_operator",
            "mcp__psexp__record_grade",
            "mcp__psexp__record_production",
            "mcp__psexp__record_reserves",
            "mcp__psexp__record_event",
            "mcp__psexp__record_outage",
            "mcp__psexp__record_logistics",
            "mcp__psexp__record_source",
            "mcp__psexp__report_issue",
            "mcp__psexp__finalise",
        ],
        permission_mode="bypassPermissions",
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

def persist_to_db(
    buf: dict[str, Any],
    field: dict[str, Any],
    meta: dict[str, Any],
    replace_existing: bool = False,
) -> None:
    """Upsert the buffer into ``production_sites.*`` + write ``profile.json``.

    By default (``replace_existing=False``) appends to the child tables and
    tags every new row with the producing ``run_id``; prior runs' rows are
    preserved. After the inserts complete, a consistency scan detects "same
    logical entity emitted under multiple runs" via simple natural keys
    (same operator + start_year, same event_type + start_date ±90d, etc.)
    and writes findings to ``exploration_runs.inconsistencies`` (JSONB).
    Pass ``replace_existing=True`` to delete every child row for this
    field_id BEFORE inserting — useful when you want a clean replacement.
    """
    fid = field["field_id"]

    with _conn() as conn:
        with conn.cursor() as cur:
            # 0. Optional --replace: clear every child row for this field.
            if replace_existing:
                for tbl in (
                    "production_sites.production_timeseries",
                    "production_sites.field_outages",
                    "production_sites.logistics_links",
                    "production_sites.events",
                    "production_sites.reserves",
                    "production_sites.field_grades",
                    "production_sites.field_operators",
                    "production_sites.sources",
                ):
                    cur.execute(f"DELETE FROM {tbl} WHERE field_id = %s", (fid,))

            # 1. Patch master row from update_field_meta calls.
            m = buf.get("field_meta", {})
            aliases_list = None
            if m.get("aliases"):
                aliases_list = [a.strip() for a in str(m["aliases"]).split(",") if a.strip()]

            cur.execute(
                """
                UPDATE production_sites.field SET
                    aliases               = COALESCE(%s, aliases),
                    basin                 = COALESCE(NULLIF(%s, ''), basin),
                    play                  = COALESCE(NULLIF(%s, ''), play),
                    state                 = COALESCE(NULLIF(%s, ''), state),
                    onshore_offshore      = COALESCE(NULLIF(%s, ''), onshore_offshore),
                    latitude              = COALESCE(%s, latitude),
                    longitude             = COALESCE(%s, longitude),
                    discovery_year        = COALESCE(%s, discovery_year),
                    first_production_year = COALESCE(%s, first_production_year),
                    status                = COALESCE(NULLIF(%s, ''), status),
                    field_type            = COALESCE(NULLIF(%s, ''), field_type),
                    notes                 = COALESCE(NULLIF(%s, ''), notes),
                    last_explored_at      = now()
                WHERE field_id = %s
                """,
                (
                    aliases_list,
                    m.get("basin") or "",
                    m.get("play") or "",
                    m.get("state") or "",
                    m.get("onshore_offshore") or "",
                    m.get("latitude"),
                    m.get("longitude"),
                    m.get("discovery_year"),
                    m.get("first_production_year"),
                    m.get("status") or "",
                    m.get("field_type") or "",
                    m.get("notes") or "",
                    fid,
                ),
            )

            # 1b. Exploration run audit — insert FIRST (after master row) so
            #     subsequent rows (production_monthly in particular) can carry
            #     run_id back to this audit row. Mirrors the refinery agent.
            cur.execute(
                """
                INSERT INTO production_sites.exploration_runs
                    (field_id, started_at, finished_at, model, tool_calls,
                     tokens_in, tokens_out, cost_usd, status, error, summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING run_id
                """,
                (
                    fid, meta.get("started_at"), meta.get("finished_at"),
                    meta.get("model"), meta.get("tool_calls"),
                    meta.get("tokens_in"), meta.get("tokens_out"),
                    meta.get("cost_usd"), meta.get("status"),
                    meta.get("error"), buf.get("summary"),
                ),
            )
            run_id = cur.fetchone()[0]

            # 2. Sources first — keep source_id by URL.
            source_ids: dict[str, int] = {}
            for s in buf.get("sources", []):
                cur.execute(
                    """
                    INSERT INTO production_sites.sources
                        (field_id, url, title, publisher, document_type,
                         published_at, notes, run_id)
                    VALUES (%s, %s, %s, %s, %s, NULLIF(%s, '')::date, %s, %s)
                    RETURNING source_id
                    """,
                    (
                        fid, s.get("url"), s.get("title"), s.get("publisher"),
                        s.get("document_type"),
                        (s.get("published_date") or "").strip(),
                        s.get("notes"), run_id,
                    ),
                )
                source_ids[s.get("url") or ""] = cur.fetchone()[0]

            def _src(url: str | None) -> int | None:
                return source_ids.get(url or "")

            # 3. Operators.
            for op in buf.get("operators", []):
                end_year = op.get("end_year")
                if end_year == 0:
                    end_year = None
                cur.execute(
                    """
                    INSERT INTO production_sites.field_operators
                        (field_id, operator_name, parent_corp, role, share_pct,
                         start_year, end_year, source_id, run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        fid, op["operator_name"], op.get("parent_corp"),
                        op.get("role"), op.get("share_pct"),
                        op.get("start_year") or None, end_year,
                        _src(op.get("source_url")), run_id,
                    ),
                )

            # 4. Grades.
            for g in buf.get("grades", []):
                cur.execute(
                    """
                    INSERT INTO production_sites.field_grades
                        (field_id, grade_name, api_gravity, sulphur_pct,
                         share_pct, period_start, period_end, source_id, notes, run_id)
                    VALUES (%s, %s, %s, %s, %s,
                            NULLIF(%s, '')::date, NULLIF(%s, '')::date,
                            %s, %s, %s)
                    """,
                    (
                        fid, g["grade_name"], g.get("api_gravity"),
                        g.get("sulphur_pct"), g.get("share_pct"),
                        (g.get("period_start") or "").strip(),
                        (g.get("period_end") or "").strip(),
                        _src(g.get("source_url")), g.get("notes"), run_id,
                    ),
                )

            # 5. Production timeseries — one row per agent observation. Sparse,
            #    step-function: the value carries forward until the next
            #    observation overrides it. Volume metrics are silently skipped
            #    (they don't have a step-function interpretation).
            for p in buf.get("production", []):
                metric = (p.get("metric") or "").strip()
                if metric and metric not in _ALLOWED_PRODUCTION_METRICS:
                    # accept legacy callers but record a note; data is still
                    # written so we don't silently drop research effort
                    pass
                obs_date = (p.get("observation_date") or "").strip()
                if not obs_date:
                    # malformed: agent didn't provide a date — skip
                    continue
                cur.execute(
                    """
                    INSERT INTO production_sites.production_timeseries
                        (field_id, observation_date, metric, value,
                         method, confidence, source_id, notes, run_id)
                    VALUES (%s, %s::date, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (field_id, observation_date, metric) DO UPDATE SET
                        value      = EXCLUDED.value,
                        method     = EXCLUDED.method,
                        confidence = EXCLUDED.confidence,
                        source_id  = EXCLUDED.source_id,
                        notes      = EXCLUDED.notes,
                        run_id     = EXCLUDED.run_id
                    """,
                    (
                        fid, obs_date, metric, p.get("value"),
                        p.get("method"), p.get("confidence"),
                        _src(p.get("source_url")), p.get("notes"),
                        run_id,
                    ),
                )

            # 6. Reserves.
            for r in buf.get("reserves", []):
                cur.execute(
                    """
                    INSERT INTO production_sites.reserves
                        (field_id, as_of_date, category, commodity, volume,
                         unit, source_id, notes, run_id)
                    VALUES (%s, NULLIF(%s, '')::date, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        fid, (r.get("as_of_date") or "").strip(),
                        r.get("category"), r.get("commodity"),
                        r.get("volume"), r.get("unit"),
                        _src(r.get("source_url")), r.get("notes"), run_id,
                    ),
                )

            # 7. Events.
            for e in buf.get("events", []):
                cur.execute(
                    """
                    INSERT INTO production_sites.events
                        (field_id, event_type, start_date, end_date,
                         description, source_id, run_id)
                    VALUES (%s, %s,
                            NULLIF(%s, '')::date, NULLIF(%s, '')::date,
                            %s, %s, %s)
                    """,
                    (
                        fid, e.get("event_type"),
                        (e.get("start_date") or "").strip(),
                        (e.get("end_date") or "").strip(),
                        e.get("description"),
                        _src(e.get("source_url")), run_id,
                    ),
                )

            # 7b. Outages (TAR + maintenance + hurricanes + curtailments).
            #     Time-windowed, tagged with run_id for traceability.
            for o in buf.get("outages", []):
                cur.execute(
                    """
                    INSERT INTO production_sites.field_outages
                        (field_id, start_date, end_date, outage_type,
                         impact_pct, description, source_id, run_id)
                    VALUES (%s,
                            NULLIF(%s, '')::date, NULLIF(%s, '')::date,
                            %s, %s, %s, %s, %s)
                    """,
                    (
                        fid,
                        (o.get("start_date") or "").strip(),
                        (o.get("end_date") or "").strip(),
                        o.get("outage_type"),
                        o.get("impact_pct") or None,
                        o.get("description"),
                        _src(o.get("source_url")),
                        run_id,
                    ),
                )

            # 8. Logistics.
            for L in buf.get("logistics", []):
                cur.execute(
                    """
                    INSERT INTO production_sites.logistics_links
                        (field_id, asset_type, asset_name, operator,
                         capacity_bpd, direction, notes, source_id, run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        fid, L.get("asset_type"), L.get("asset_name"),
                        L.get("operator"), L.get("capacity_bpd") or None,
                        L.get("direction") or "outbound",
                        L.get("notes"), _src(L.get("source_url")), run_id,
                    ),
                )

            # 9. Consistency scan + agent-notes persistence — runs after all
            #    inserts so it can detect "same logical entity emitted under
            #    multiple runs" via simple natural keys.
            inconsistencies = _scan_inconsistencies(cur, fid)
            cur.execute(
                """
                UPDATE production_sites.exploration_runs
                   SET inconsistencies     = %s::jsonb,
                       has_inconsistencies = %s,
                       agent_notes         = %s::jsonb
                 WHERE run_id = %s
                """,
                (
                    json.dumps(inconsistencies),
                    bool(inconsistencies),
                    json.dumps(buf.get("agent_notes", [])),
                    run_id,
                ),
            )

    # 10. Also write profile.json.
    out_dir = PRODUCTION_SITES_OUT / fid
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "field":    field,
        "result":   buf,
        "run_meta": {**meta, "started_at": str(meta.get("started_at")),
                              "finished_at": str(meta.get("finished_at"))},
    }
    (out_dir / "profile.json").write_text(
        json.dumps(profile, indent=2, default=str)
    )
