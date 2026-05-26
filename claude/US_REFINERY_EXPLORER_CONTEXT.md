# US Refinery Explorer — Context

Drop-in context for any new Claude conversation taking over this task.

## TL;DR

Building a **US_refinery_explorer agent** that loops over the 115 US refineries in `oil_network.nodes`, web-researches each one (10-Ks, investor decks, EIA, trade press, news), and persists structured findings to a new `refineries.*` Postgres schema + per-refinery `profile.json` files. Powered by the **Claude Agent SDK** with subscription auth (Pedro's Claude Code Max $100/month Agent SDK credit) — **not** the raw `anthropic` SDK with API key.

Stage: schema applied; smoke test **passed** end-to-end on `ref_american_bradford` (21 min, 185 tool calls, $4.41, all 8 tables populated). One custom MCP tool (`query_eia_timeseries`) needs a SQL fix before the remaining 2 calibration refineries are run, because the agent burned tool calls working around it via direct EIA WebFetch.

## What Pedro asked for

> "Build an agent called US_refinery_explorer. For each US refinery in the DB, get monthly timeseries + detailed info (grades, slate, owner, operator, financial statements, as many details as possible from public sources). Build an agent, then a notebook to load refineries and go refinery by refinery. Save data as files in output on Stage 2. Create schema `refineries` referencing the main schema for refinery codes, plus a table for the produced monthly timeseries per refinery."

Calibration on 3–5 refineries first, then full sweep of 115.

## Where things live (Stage2)

| Path | Purpose |
|---|---|
| `Stage2/code/us_refinery_explorer.py` | The agent module. `list_us_refineries()`, `explore_refinery_async()`, `persist_to_db()`, `already_explored()`. Uses `claude_agent_sdk.query()` with 8 custom MCP tools (`record_unit`, `record_slate`, `record_event`, `record_financial`, `record_monthly`, `record_source`, `finalise`, `query_eia_timeseries`) + WebSearch + WebFetch. |
| `Stage2/code/migrations/create_refineries_schema.py` | Idempotent DDL for the `refineries.*` schema (CREATE TABLE IF NOT EXISTS for 8 tables). |
| `Stage2/code/explore_us_refineries.ipynb` | The orchestrator notebook (6 cells). Cell 2 applies the migration, cell 4 has the `MODE` toggle (`'calibration'` \| `'full'` \| `'custom'`), cell 5 runs the agent, cell 6 inspects outputs. |
| `Stage2/requirements.txt` | `claude-agent-sdk>=0.1` added (currently installed: `0.2.87`). |
| `Stage2/outputs/refineries/<refinery_id>/profile.json` | Per-refinery output file (not yet written — calibration failed before any agent run produced data). |

Path-resolution contract: the module imports `OUTPUTS_DIR` from `code/paths.py` (existing project convention). Don't hand-roll filesystem paths.

## Schema (`refineries.*`, in DB `eia_crude`)

Eight tables, all FK-tied to `oil_network.nodes(node_id)`:

| Table | PK | Purpose |
|---|---|---|
| `refinery` | `refinery_id` | Master row per refinery (mirrors key facts from `oil_network` for joins: name, corporation, operator, site, state, PADD, rdist, capacity, duoarea_code, last_explored_at). FK to `oil_network.nodes`. |
| `sources` | `source_id` (serial) | Citation log — every URL the agent visited (`url`, `title`, `publisher`, `document_type`, `published_at`, `fetched_at`, `notes`). |
| `process_units` | `(refinery_id, unit_type)` | FCC, coker, hydrocracker, etc. with `capacity_bpd`. |
| `slate` | `(refinery_id, period_start, grade_name)` | Crude-slate observations: `api_gravity`, `sulphur_pct`, `share_pct`. |
| `events` | `event_id` (serial) | Turnarounds, fires, expansions, ownership changes. |
| `financials` | `(refinery_id, period_start, period_type)` | 10-K/10-Q extracts: throughput, utilisation, revenue, EBITDA per Q1/Q2/Q3/Q4/FY. |
| `runs_monthly` | `(refinery_id, month, metric)` | **Synthesised monthly timeseries** — `crude_runs_bpd`, `utilisation_pct`, `capacity_bpd`. Carries `method` (eia_attributed, financials_quarterly_split, news_turnaround_adjusted, capacity_utilisation_baseline) and `confidence` (high/medium/low). |
| `exploration_runs` | `run_id` (serial) | One row per agent invocation: model, tool_calls, tokens, cost, status, summary, error. Used for resume-aware skip via `already_explored(refinery_id)`. |

Schema is **already applied** — verified by cell 2 of the last notebook run. `\dt refineries.*` in psql will show all 8 tables empty.

## Authentication / billing model

- The Agent SDK ships a bundled `claude.exe` at `.venv/Lib/site-packages/claude_agent_sdk/_bundled/claude.exe`. No separate Claude Code CLI install was needed.
- Auth uses Pedro's **Claude Code Max** subscription, which includes a **$100/month Agent SDK credit**. Smoke test confirmed it works ("What is the capital of France?" → "Paris.", `total_cost_usd: 0.07`).
- No `ANTHROPIC_API_KEY` is set in env, and we don't need one.
- Model: `claude-sonnet-4-6` (configurable via `MODEL` in notebook cell 4).

## Running it

```powershell
# venv lives at C:\Users\PedroPorfirio\OneDrive - Jabuticaba\Oil Network Project\.venv
# (shared across Stage1 and Stage2)

# Open Stage2/code/explore_us_refineries.ipynb and run cells in order:
#   Cell 1: setup
#   Cell 2: applies the schema (idempotent — no-op if already there)
#   Cell 3: lists 115 US refineries from the DB
#   Cell 4: edit MODE = 'calibration' | 'full' | 'custom'
#   Cell 5: runs the agent, persists each refinery before moving to the next
#   Cell 6: prints DB row counts + profile summary for one refinery
```

`MODE = 'calibration'` runs 3 hand-picked refineries:
- `ref_p66_bayway` — Phillips 66, Linden NJ, 258 kbpd (major, lots of public data)
- `ref_pbf_delaware_city` — PBF Energy, 190 kbpd (mid-size, FCC + coker)
- `ref_american_bradford` — American Refining Group, 11 kbpd (small, sparse data)

`FORCE = False` makes the loop resume-aware: refineries with a successful `exploration_runs` row are skipped.

## Bug history

**1. psycopg2 re-entrant context manager — FIXED + VERIFIED**

First nbconvert calibration run failed for all 3 refineries with:

```
psycopg2.errors.ProgrammingError: the connection cannot be re-entered recursively
```

Cause: `persist_to_db()` had `with _conn() as conn:` wrapping `with conn, conn.cursor() as cur:` — the inner `with conn` re-entered the connection's context manager. **Fixed** by collapsing to:

```python
with _conn() as conn:
    with conn.cursor() as cur:
        ...
```

at `code/us_refinery_explorer.py` (around line 471 — `persist_to_db()` body). Verified working by the smoke-test run below.

**2. `query_eia_timeseries` MCP tool returns DB column errors — OPEN**

During the Bradford smoke test the agent reported (in its `finalise` summary): *"the mcp__refexp__query_eia_timeseries tool returned a persistent DB column error throughout, requiring direct EIA WebFetch to obtain district series data."* It worked around it, but burned tool calls doing so (185 tool calls vs ~50 the prompt budgets).

Likely cause: my SQL probes `t.attributes->>'duoarea'` and joins `oil_network.timeseries_data` to `oil_network.timeseries` `USING (timeseries_id)`. The actual column / JSONB key names may differ. **Diagnose** by running in psql:

```sql
\d+ oil_network.timeseries
\d+ oil_network.timeseries_data
SELECT DISTINCT jsonb_object_keys(attributes) FROM oil_network.timeseries WHERE attributes IS NOT NULL;
```

then fix the SQL in the `query_eia_timeseries` tool body in `code/us_refinery_explorer.py`.

**3. libzmq "Socket operation on non-socket" — observed once, did not recur**

Appeared during the first nbconvert run (Windows Proactor event loop). Did not recur in the standalone Python smoke-test run (no Jupyter kernel). If it bites in production, set `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` at notebook start.

## Smoke test result (`ref_american_bradford`, 2026-05-25)

Standalone Python run (not via nbconvert), 17:11–17:32, **21 m 32 s**, **185 tool calls**, **$4.41** API-rate cost. Status: **success**.

| Output | Count | Notes |
|---|---:|---|
| Process units | 5 | crude_distillation, naphtha_splitter (implied), catalytic_reformer (with 2026 platformer fire noted), hydrotreater (3500 bpd, c.2007–09), lube_oil_mek_dewaxing, solvent_deasphalting_rose_extract |
| Slate observations | 3 | Pennsylvania (42 °API, 0.25% S, 60% share, 1997), Ohio (38 °API, 0.40% S, 40% share, 1997), blended PA/OH/NY/WV paraffinic (41 °API, 0.30% S, 100%, 2008) |
| Events | 11 | 1997 Halloran acquisition from Witco/Kendall; Dec 2021 Halloran Jr. death + transition to son Neil; multiple inferred turnarounds 2019–2025 |
| Financial periods | 1 | 2007 trade-press estimate ~$250M revenue (private company, no SEC filings) |
| Monthly throughput points | 86 | Jan 2019 → Feb 2026, all `eia_attributed` at ~11% capacity share of Appalachian No. 1 district, all `low` confidence |
| Sources cited | 11 | |
| `profile.json` on disk | 51 KB | `Stage2/outputs/refineries/ref_american_bradford/profile.json` |

Agent's `finalise` summary captured the refinery's character correctly: family-owned specialty refinery (oldest continuously running in North America), all-domestic Appalachian/Ohio paraffinic crude, base-oil and wax products, no FCC/coker/alkylation/isomerisation. Worth reading the full string in `exploration_runs.summary` for a model of what good output looks like.

## Cost projection

Bradford: $4.41 at API rates (drawn from the $100/month Agent SDK credit). Naive extrapolation × 115 = **$507**, well over the $100/mo credit.

Two levers to bring this down:

1. Fix `query_eia_timeseries` so the agent stops WebFetching EIA pages it could read from the local DB (saves ~30–50 tool calls per refinery).
2. Tighten `SYSTEM_PROMPT`: cap tool calls at ~50, narrow turnaround inference to "confirmed only" rather than inferring from EIA district dips.

Target after tuning: **$1–2/refinery → $115–230 total sweep**.

## Current state

- `refineries.*` schema: applied, populated for `ref_american_bradford` only (1/115). All 8 tables have 1+ row for that refinery.
- `Stage2/outputs/refineries/ref_american_bradford/profile.json`: 51 KB on disk.
- Bradford `exploration_runs` row has `status='success'`, so `already_explored('ref_american_bradford')` returns True — next calibration run will skip it automatically unless `FORCE=True`.

## Next steps (in order)

1. **Fix the `query_eia_timeseries` MCP tool** in `code/us_refinery_explorer.py`. See "Bug history #2" above for diagnosis steps.
2. **Tighten `SYSTEM_PROMPT`** — cap tool calls at ~50, narrow turnaround inference to "confirmed only".
3. **Run the remaining 2 calibration refineries** via the notebook (`MODE='calibration'` + `FORCE=False`, Bradford auto-skipped):
   - `ref_p66_bayway` — Phillips 66, Linden NJ, 258 kbpd (major, lots of public data)
   - `ref_pbf_delaware_city` — PBF Energy, Delaware City, 190 kbpd (mid-size, FCC + coker)
4. **Review the 3 calibration `profile.json` files side-by-side.** Decide if agent output quality is good enough for the thesis. Things to check:
   - Process units found beyond what EIA's refcap25 had (96/115 are missing FCC/coker/hydrocracker flags currently).
   - Monthly series defensibility — what fraction is `eia_attributed`, `financials_quarterly_split`, etc.?
   - Sources actually relevant (10-Ks, SEC, EIA — not random news aggregators).
5. **Full sweep**: edit cell 4 to `MODE = 'full'` and run. Per-refinery cost from tuned calibration × 115 = firmer budget.

## Key data facts

- **115 US refineries** in `oil_network.nodes` (`a.node_subtype = 'refinery' AND l.country = 'US'`). Total nameplate capacity ~17.58M bpd, vs EIA-published ~18.4M for 2025 (~95% coverage). The ~825 kbpd gap is small/idle refineries the refcap25 sheet didn't enumerate.
- **EIA refcap25 coverage of attribute fields** (out of 115):
  - 112/115 have: site, corporation, capacity_bpd, duoarea_code, data_source, rdist_label, company_name
  - **Only 19/115** have: has_fcc, has_coker, has_hydrocracker, operator, preferred_slate, nelson_complexity_index, capacity_bd
- **The agent's main job** is filling in those sparse fields for the other 96 refineries, plus building the monthly timeseries which doesn't exist anywhere in the DB at per-refinery granularity (EIA series are PADD-aggregated).

## Conventions to respect

- **British spelling** in any natural-language strings (matches the thesis project convention — `utilisation`, `behaviour`, `modelled`).
- **Path-resolution contract**: import from `code/paths.py` (`OUTPUTS_DIR`, `CODE_DIR`, etc.) — don't hand-roll paths.
- **Don't relax design principles** without explicit discussion with Pedro (see `Stage2/claude/CLAUDE.md` section 2).
- **HANDOVER.md sync** — Pedro switches machines often; update `Stage2/claude/HANDOVER.md` after substantive structural changes (per his standing feedback memory).

## Things this handover doesn't cover

- Whether the agent's output quality is actually good enough for the thesis. That's an empirical question — check profile.json content after calibration.
- Cost projection beyond the $100/mo credit. Calibration cost data will firm this up.
- Whether the synthesised monthly series should also be written into `oil_network.timeseries_data` (the resolver's source of truth) — current design keeps `refineries.*` separate. Pedro hasn't asked for that integration yet.
- The libzmq assertion failure cause. If it bites in production, set `WindowsSelectorEventLoopPolicy` early.
