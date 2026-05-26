# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read these first

- [claude/CLAUDE.md](claude/CLAUDE.md) — **read in full before making any changes.** Contains the thesis design principles (Section 2 — committed, must not drift), Pedro's style conventions, and how to work with him.
- [claude/PROJECT_STATE.md](claude/PROJECT_STATE.md) — current live DB numbers (251 nodes, 1,870 variables, 291,564 resolved rows), stage status, and "what runs on a fresh machine" recipe.
- [claude/HANDOVER.md](claude/HANDOVER.md) — pass-by-pass narrative history; resume-here doc.
- [claude/NOTEBOOKS.md](claude/NOTEBOOKS.md) — orchestrator chain documentation: how every notebook and script in `code/` plugs together.
- [RESTORE_STAGE_1.md](RESTORE_STAGE_1.md) — runbook to restore the `stage_1_complete` tag from snapshot or rebuild from scratch.

## Repository layout

This is the active project directory (`Thesis/clean/`). The sibling `old/` directory under `Thesis/` holds history that nothing active reads.

- [code/](code/) — all Python + notebooks (flat). Active surface: resolvers, renderers, PDF generators, audits, DDL/view scripts, orchestrator notebooks.
- [code/migrations/](code/migrations/) — 23 one-shot scripts that mutate the DB to its current state (numbered passes, `repoint_*`, `split_*`, `wire_*`, `refactor_*`, `promote_*`, `patch_*`, `add_*`). Run by the orchestrator on a clean rebuild; idempotent.
- [code/old/](code/old/) — retired scripts, kept for reference. Nothing active imports here.
- [claude/](claude/) — project memory: design docs, handover, state. No code here.
- [config/asset_graph.json](config/asset_graph.json) — the seed file (loaded once by `load_asset_graph.ipynb`).
- [outputs/html/](outputs/html/) — the 5 canonical HTML explorers (resolver-driven, with embedded staleness beacons).
- [outputs/docs/](outputs/docs/) — thesis drafts (v17 → v37), reference PDFs (`Design_Principles.pdf`, `Resolver_Walkthrough.pdf`, `Scenario_Construction.pdf`, `Graph_Construction.pdf`), diagrams.

**Path-resolution contract.** Every script that reads or writes a file imports the relevant constant from [code/paths.py](code/paths.py) (`CODE_DIR`, `CONFIG_DIR`, `HTML_DIR`, `DOCS_DIR`, `ASSET_GRAPH_JSON`). Do not hand-roll filesystem paths — relocating the project should be a one-line edit to `paths.py`.

## Commands

Activate the venv first (it lives at `../../.venv` relative to this directory — i.e. at the repo root, one level above `Thesis/`):

```powershell
..\..\.venv\Scripts\Activate.ps1                       # PowerShell
# or
..\..\.venv\Scripts\python.exe <script>.py             # explicit interpreter
```

**Sanity check after any rebuild** (always run this before claiming the orchestrator passed):

```powershell
..\..\.venv\Scripts\python.exe code\verify_state.py
```

Expected headline: 251 assets, 1,870 variables, 291,564 resolved rows, 0 unresolved, 0 TS-binding collisions, 0 capacity violations.

**Full clean rebuild** (`DROP SCHEMA oil_network CASCADE` + run the master notebook; ~6–8 min):

```powershell
cd code
..\..\..\.venv\Scripts\jupyter.exe nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 initialize_oil_network.ipynb
```

The master orchestrator is `code/initialize_oil_network.ipynb` (4 stages, 38 steps in stage 4). Individual stages can be re-run standalone — see [claude/NOTEBOOKS.md](claude/NOTEBOOKS.md) for the chain.

**Re-resolve a scenario without rebuilding** (resolver auto-refreshes analytic views at the end):

```powershell
..\..\.venv\Scripts\python.exe code\resolve_scenario.py
```

**Regenerate the 5 HTML explorers** (orchestrator picks the stale subset by reading the embedded run_id beacon; use `--force` to rebuild all):

```powershell
..\..\.venv\Scripts\python.exe code\regenerate_htmls.py            # only stale
..\..\.venv\Scripts\python.exe code\regenerate_htmls.py --force    # all
..\..\.venv\Scripts\python.exe code\regenerate_htmls.py --list     # status only
```

**Refresh materialised views** (the resolver does this automatically; only needed standalone after a migration):

```powershell
..\..\.venv\Scripts\python.exe code\refresh_views.py --structural  # L2/L3
..\..\.venv\Scripts\python.exe code\refresh_views.py --analytic    # L4
..\..\.venv\Scripts\python.exe code\refresh_views.py               # both
```

**Regenerate reference PDFs** (markdown → xhtml2pdf, plus one reportlab generator):

```powershell
..\..\.venv\Scripts\python.exe code\pdf_design_principles.py
..\..\.venv\Scripts\python.exe code\pdf_resolver_walkthrough.py
..\..\.venv\Scripts\python.exe code\pdf_scenario_construction.py
..\..\.venv\Scripts\python.exe code\pdf_graph_construction.py
```

**Audits** (non-blocking; surface drift):

```powershell
..\..\.venv\Scripts\python.exe code\audit_capacity_violations.py
..\..\.venv\Scripts\python.exe code\audit_resolution_anomalies.py
..\..\.venv\Scripts\python.exe code\compare_resolvers.py    # diff resolve_scenario vs recursive_resolver
```

**Snapshot restore** (fast path — seconds instead of 6–8 min; needs the `.dump` from OneDrive at `Oil Network Project/snapshots/`):

```powershell
$env:PGPASSWORD = 'eia_password'
& 'C:\Program Files\PostgreSQL\18\bin\pg_restore.exe' -h localhost -U eia_user -d eia_crude -n oil_network --clean --if-exists "<path-to>\oil_network_stage_1_complete.dump"
```

No test suite or linter is configured — `verify_state.py` plus the audits are the project's correctness gates.

## Architecture (big picture)

This is a **thesis project** about asset-centric temporal graphs for crude-oil logistics. The code is the empirical artefact behind the thesis prose; the thesis lives in `outputs/docs/Master_Thesis_Pedro_Porfirio_v37.{docx,pdf}`. The framework's contribution is the **representation and consistency guarantees**, not forecasting (forecasting is explicit future work — see Section 1 of `claude/CLAUDE.md`).

### The data model has three layers (Postgres, schema `oil_network`)

1. **Asset graph (static):** `assets`, `nodes`, `locations`, `node_configuration` — physical and abstract nodes (251 total: 217 physical + 34 abstract). Loaded once from `config/asset_graph.json` by `load_asset_graph.ipynb`.
2. **Variables (the data model):** `variables` is the single source of truth (1,870 rows). Every (variable_type, commodity, node, related_node) tuple is unique. `variable_assignments` binds each variable in each scenario to **either** a `timeseries_id` (observed) **or** a `formula` (derived) — enforced by `CHECK (num_nonnulls(timeseries_id, formula) = 1)`. This is **Axiom 5 / Principle 2.8** in the design docs and is the single most important invariant.
3. **Scenarios + time series:** `timeseries` (catalogue) + `timeseries_data` (68,793 vintaged rows). `scenario_resolved_values` (291,564 rows) is the materialised output of the resolver — one row per (scenario, variable, date).

**Edges, partition trees, node status are all *views* over the variables collection**, not separate tables. Twelve materialised views (`v_flow_edges`, `v_aggregation_edges`, `v_partition_tree`, `v_node_status`, `v_partition_sums`, `v_node_balance_check`, `v_aggregation_consistency`, etc.) plus 5 regular views. See Section 2.4 of `claude/CLAUDE.md` — "formula-implies-relation" is the principle that makes this work.

### The resolver

[code/resolve_scenario.py](code/resolve_scenario.py) is the primary implementation: topological sort + a mirror post-pass for paired flow edges. [code/recursive_resolver.py](code/recursive_resolver.py) is an output-equivalent fixed-point alternative; `compare_resolvers.py` diffs them. Both write to `scenario_resolved_values` with a `source` column recording the dispatch rule (`observed`, `zero`, `latent`, `derived`, `partial`, `unresolved`). Healthy runs have **0 unresolved** rows.

### The renderers

[code/network_graph.py](code/network_graph.py) (`NetworkGraph(scenario_id)`) is the in-memory engine all renderers share. It loads from the L3/L4 mat views and `scenario_resolved_values` — never from `variable_assignments` directly. Loads in ~300 ms. Exposes `partition_children`, `descendants`, `status`, `value`, `node_balance`, `inflows_to`, `outflows_from`.

Five renderers, all driven by NetworkGraph + the layered views:

- `make_balance_resolver_ui.py` — per-node P/C/I/O/B/S table
- `make_hierarchy_resolver_ui.py` — drill-down tree
- `make_map_resolver_ui.py` — geographic map
- `make_partition_map.py` — partition tree explorer
- `make_node_neighbors_map.py` — neighbourhood explorer

Each embeds a metadata beacon naming the resolver `run_id` that produced it; `regenerate_htmls.py` uses that to detect staleness.

### The thesis prose

The thesis prose is **not generated from code**. It is hand-edited DOCX (`outputs/docs/Master_Thesis_Pedro_Porfirio_v37.docx`). Section 8.1 of `claude/CLAUDE.md` documents Pedro's DOCX editing workflow if structural edits are needed (`unpack.py` / `pack.py` on `unpacked/word/document.xml`); for prose edits, just open in Word. **When emitting a new thesis docx, always also produce a matching PDF** — Pedro reads on a Remarkable (see `[Pair thesis docx with PDF]` in user memory).

The four reference PDFs (`Design_Principles.pdf`, `Resolver_Walkthrough.pdf`, `Scenario_Construction.pdf`, `Graph_Construction.pdf`) **are** generated — from the corresponding markdown source files in `claude/` via the `pdf_*.py` scripts.

## Conventions worth knowing before editing

- **British spelling** in all thesis-facing prose ("modelled", "organised", "colour"). Code identifiers may follow conventional Python casing.
- **Don't relax a design principle without explicit discussion.** The 10 principles in Section 2 of `claude/CLAUDE.md` are committed; Pedro will notice drift.
- **The orchestrator is idempotent.** A `DROP SCHEMA oil_network CASCADE` + master notebook produces the same state every time. Migrations in `code/migrations/` are written to be re-runnable.
- **Single source of truth.** No structural fact about the graph is stored twice. If you're tempted to add a column that duplicates information already in `variable_assignments`, write a view instead.
- **`stage_1_complete` is a frozen tag.** Code on `main` past 2026-05-18 is post-stage-1; the snapshot and git tag are restore points. Don't force-push to `main` or move the tag.
