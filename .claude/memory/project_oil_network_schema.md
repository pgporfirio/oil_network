---
name: oil_network schema (current build)
description: Pedro's relational schema for the asset-centric oil-flow model in PostgreSQL — current state as of 2026-05-01. The older oil_logistics_network has been dropped from the DB.
type: project
originSessionId: 77985a0a-6dc2-4425-beea-7ddad2ee3c79
---
## Current state (2026-05-01)

Two production schemas live in local PostgreSQL `eia_crude`:

- **`oil_network`** — the asset-centric model schema. Active source of truth.
- **`source_eia`** — raw EIA crude-oil time series, vintaged. Separate schema, kept clean from `oil_network` (loader from one to the other is a downstream step that hasn't been built yet).

The pre-2026-04-29 `oil_logistics_network` schema was DROPPED on 2026-05-01 — it had been abandoned for ~10 days but was leaving stale `edges` and `resolution_hierarchy` tables visible in DBeaver, which confused things. Gone now.

## Files (in `Thesis/Code/`)

- `build_oil_network.ipynb` — full DDL, idempotent (drops + recreates schema).
- `load_asset_graph.ipynb` — incremental loader from `asset_graph/asset_graph.json`. UPSERTs everywhere.
- `initialize_oil_logistics_network.ipynb` — one-shot: build + load + verify + plot. Run this first on any new machine.
- `load_eia_data.ipynb` — raw EIA pull into `source_eia`. Incremental by default; `RESET=True` for fresh-clone refetch.
- `oil_network_design.md` — all 22 design decisions with WHY for each.
- `HANDOVER.md` — current numbers and resume-here doc.
- `asset_graph/asset_graph.json` — input data (111 nodes, 198 edges, starter coverage contract).

## Live counts

**`oil_network`:**
- 18 base tables, no views, no separate `edges`/`resolution_hierarchy` tables (edges = relational variables; resolution hierarchy = JSONB on assets).
- 111 assets / 111 nodes (96 physical + 15 abstract).
- 24 pipelines (20 US + 4 Canadian cross-border), 24 refineries (19 named + 5 PADD residuals), 7 export terminals, 4 import aggregates, 10 storage hubs.
- 140 unique flow edges (= 140 inflow + 140 outflow relational variables), 724 variables total.
- Coverage: production 113%, refining 100%, exports 100%+ headroom, Canadian pipelines ~100%.

**`source_eia`:**
- 3 tables: `eia_staging` (raw, 159,907 rows), `eia_series_catalog` (1,485 unique series), `eia_data` (144,682 vintaged facts).
- 26 EIA datasets across 7 domains (production / imports / exports / movements / refinery / stocks / S&D / weekly trade).
- PK on `eia_data` is `(series_id, observation_date, saved_date)`. Vintage-on-change: new `saved_date` row only when value differs from the latest.

## Where edges and resolution hierarchy actually live

Not as tables. Per design principle 2.4 (formula-implies-edge):
- **Edges** = pairs of relational variables in `oil_network.variables` with `related_node_id` set. The JSON `edges` array is just a convenience input format; the loader skips the 58 aggregation edges and creates `(inflow, outflow)` variable pairs from the 140 flow edges.
- **Resolution hierarchy** = `oil_network.assets.attributes.resolution_hierarchy` JSONB on every asset row.

If anyone asks "where's the edges table" — there isn't one, by design.

## What's done vs not done

**Done:** asset graph (incl. Canadian layer, 2026-04-30) + raw EIA ingest into `source_eia` (2026-05-01).

**Next workstream:**
1. EIA-series-to-asset mapping in `asset_graph/eia_series_map.json` (rule-based on `(duoarea, process)`, manual overrides for the messy ones).
2. Loader notebook from `source_eia.eia_data` → `oil_network.timeseries` + `timeseries_data`.
3. `variable_assignments` populated from the coverage contract.
4. `node_type_default_formulas` first pass.
5. Formula evaluator runtime.

## How to apply

When Pedro asks about the schema: defer to `oil_network_design.md` for the canonical answers. When proposing changes, check the design doc to see if the question was already settled. When the user mentions seeing leftover `edges` / `resolution_hierarchy` tables, check whether they're looking at the dropped `oil_logistics_network` schema — those tables never existed in `oil_network`.
