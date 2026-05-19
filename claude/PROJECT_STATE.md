# PROJECT_STATE.md — current state summary

Comprehensive snapshot of the `oil_network` project. Refreshed when something
substantive changes. For the pass-by-pass narrative history, see
[HANDOVER.md](HANDOVER.md). For commitments and design rules, see
[CLAUDE.md](CLAUDE.md).

**Stage:** Stage 2 baseline (fresh repository, continuing from the stage 1 deliverable set). Schema, resolver, mat views and thesis v45 all carry over; new work happens on top.
**Last refreshed:** 2026-05-19 (Stage 2 repo initialised; schema state from the last stage-1 rebuild — 251 nodes / 1,870 variables / 291,564 resolved rows / 12 mat views — verified clean from this repo by `code/verify_state.py`).
**Last clean rebuild verified:** 2026-05-18 — full `DROP SCHEMA → orchestrator` cycle reproduces the state below; 38 orchestrator steps.
**Git remote:** none yet (fresh start). Add one when ready: `git remote add origin <url> && git push -u origin main`.
**Latest thesis draft:** [outputs/docs/Master_Thesis_Pedro_Porfirio_v45.docx](../outputs/docs/Master_Thesis_Pedro_Porfirio_v45.docx) + matching `.pdf` (Chapters 5-7 numerics refreshed to current state in the v44 → v45 step). Historical drafts (v16-v44) are not carried into this repo by design; they live in the old `oil_network_clean` repo if needed.
**Standalone references:** `Design_Principles.pdf` (axioms / corollaries / resolution-rule canon), `Scenario_Construction.pdf` (five-stage scenario construction with axiom/corollary tags + figure), `Resolver_Walkthrough_v2.pdf`, `Graph_Construction.pdf` — all in `outputs/docs/`.

---

## 1. Where the project sits in the thesis arc

| Phase | Status |
|---|---|
| Postgres schema + asset graph load | ✓ done (one orchestrator notebook) |
| EIA timeseries ingestion (staging schema) | ✓ done (68,793 vintaged rows in `oil_network.timeseries_data`) |
| `variable_assignments` for production + refining | ✓ done |
| Scenario resolver (`resolve_scenario.py`, legacy topo+mirror) | ✓ done (0 unresolved) |
| Fixed-point resolver (`recursive_resolver.py`, alt impl) | ✓ done (output-equivalent to legacy; verified by `compare_resolvers.py`) |
| Layered materialised views (L2-L4) | ✓ done (12 mat views in `oil_network`) |
| HTML explorers (5 of them, resolver-driven) | ✓ done |
| Partition closure across every PADD | ✓ done (closes within documented partial_coverage cells) |
| Physical capacity layer (`asset_capacities`) | ✓ done (215 rows, 31 time-versioned) |
| Scenario-overlay capacity layer (`variable_constraints`) | ✓ done (table; empty by design) |
| Pipeline + production capacity backfill | ✓ done (current + historical 2015-2025) |
| U.S. crude-grade registry (commodities + hierarchy + ancestor view) | ✓ done (19 grades, 18 edges) |
| Migration-script subdirectory (`code/migrations/`) | ✓ done (23 one-shot scripts isolated from active surface) |
| Thesis prose (v45) | ✓ Chapters 1-7 + Annexes drafted; Chapters 5-7 numerics refreshed to current state |
| Section 4 axioms / corollaries aligned with DESIGN_PRINCIPLES.md | ✓ done (6 axioms + 6 corollaries incl. D-bis LOCF and E node-status-as-view) |
| Section 4.3 mass-balance scope clarified to physical nodes only | ✓ done (v26) |
| Section 4.8 dual role of formula_inputs explicit (constraint set vs operand set) | ✓ done (v23) |
| Section 3.1 Fig 3.1 (physical supply chain) | ✓ corrected in v37 (v36 had ERD in the slot by mistake) |
| Section 7.9 process-to-enforcement table | ✓ done (v36) |
| Chapter 8 LP-as-downstream-consumer | ✓ done (in v36) |
| Chapter 9 case study (single-date dispatch + Permian fan-out LP) | ✓ done (in v36) |
| Chapter 10 conclusion + future work (incl. grade-registry note) | ✓ done (v37) |
| LP exporter implementation (consumes `v_effective_constraints` + latents) | — future work (post-defence; documented in Ch 8, not implemented) |
| Per-grade resolver propagation | — future work (registry seeded; resolver still single-commodity) |

---

## 2. Reproducibility — what to run on a fresh machine

```bash
# Prerequisites: Postgres running on localhost:5432, role + database
# created by setup.ipynb on first run. EIA_API_KEY captured into
# gitignored .env. Both setup.ipynb and the orchestrator pick that up.

git clone git@github.com:pgporfirio/oil_network_clean.git
cd oil_network_clean/Thesis/clean

# Run setup.ipynb (in the project root) — it pip-installs requirements,
# captures EIA_API_KEY + Postgres credentials into a gitignored .env,
# provisions the DB role + database, then runs the master orchestrator.
```

That produces the state in section 3 below. Individual stages can be re-run alone:
- `code/initialize_oil_logistics_network.ipynb` — schema + asset graph load
- `code/initialize_oil_network_metadata.ipynb` — typed metadata
- `code/initialize_oil_network_data_loader.ipynb` — EIA staging refresh (slow; skip if data present)
- `code/initialize_oil_network_assignments.ipynb` — stage 4 (38 steps: assignments + migrations + views + resolver + audits)

To regenerate the 5 HTML visualisations:
```bash
../../.venv/Scripts/python.exe code/regenerate_htmls.py --force
```

To regenerate the four reference PDFs (`Design_Principles.pdf`, `Resolver_Walkthrough.pdf`, `Scenario_Construction.pdf`, `Graph_Construction.pdf`):
```bash
../../.venv/Scripts/python.exe code/pdf_design_principles.py
../../.venv/Scripts/python.exe code/pdf_resolver_walkthrough.py
../../.venv/Scripts/python.exe code/pdf_scenario_construction.py
../../.venv/Scripts/python.exe code/pdf_graph_construction.py
```

**Faster recovery path (avoid the EIA staging step):** restore the snapshot at `Oil Network Project/snapshots/oil_network_stage_1_complete.dump`:

```bash
pg_restore -h localhost -U eia_user -d eia_crude -n oil_network \
    --clean --if-exists oil_network_stage_1_complete.dump
```

That rehydrates assets, nodes, variables, assignments, timeseries_data, scenario_resolved_values, and all materialised views in seconds — no EIA API calls, no orchestrator re-run.

---

## 3. Current state of the live DB (after clean rebuild)

### 3.1 Numbers

| Object | Count |
|---|---:|
| Assets (217 physical + 34 abstract) | **251** |
| Nodes (in the starter graph) | 251 |
| Variables | 1,870 |
| `variable_assignments` (explicit overrides) | 975 |
| `node_type_default_formulas` | 76 |
| Active timeseries (TS-bound to a variable) | 90 |
| `timeseries_data` rows (vintaged facts) | 68,793 |
| `scenario_resolved_values` (starter scenario) | **291,564** |
| `asset_capacities` (seeded + time-versioned) | 215 (31 time-versioned) |
| `variable_constraints` | 0 (empty; populated per scenario as needed) |
| `commodities` (incl. crude-grade registry) | 19 |
| `commodity_hierarchy` parent-child edges | 18 |

### 3.2 Latest resolver run (`scenario_resolver_runs.run_id = 13`)

| Dispatch rule | Count |
|---|---:|
| observed (TS lookup) | 90 |
| zero (structural) | 542 |
| latent | 767 |
| arithmetic (sum / alias / arithmetic — unified) | 451 |
| sum (canonical aggregate) | 5 |
| reverse_mirror | 15 |
| **unresolved** | **0** |

`scenario_resolved_values.source` breakdown across the 291,564 written rows:

| source | rows |
|---|---:|
| latent | 119,652 |
| zero | 84,552 |
| partial | 57,096 |
| derived | 16,224 |
| observed | 14,040 |

The `partial` rows (~19.6%) are NULL-valued by construction: a derived variable whose arithmetic input is missing on that date writes a row carrying `source='partial'` and a note rather than silently skipping the date. Used by `v_aggregation_consistency` to classify cells as partial_coverage (yellow) instead of inconsistent (red).

### 3.3 Audit invariants

- **TS-binding uniqueness:** 0 collisions (90 distinct TS in 90 bindings, perfect 1:1).
- **Capacity violations:** 0 (every observed/derived value falls within its declared capacity range, where one is declared).
- **Resolver unresolved:** 0 (every variable resolves; partial rows carry explanatory notes).
- **`v_aggregation_consistency`** (6,864 cross-checks): 3,411 ok + 3,453 partial_coverage + 0 inconsistent. The label "inconsistent" is retired; UI renders yellow = some missing/latent children, red = real divergence with no missing children.
- **`v_resolution_anomalies`:** 1,914 long_locf_run (low) + 87 negative_derived (low). All flagged as advisory; none block the orchestrator.
- **Partition closure @ 2024-12-01:** USA + PADDs return small residual gaps that reflect documented EIA publication inconsistencies (USA 909 kbd across known months) and the PADD5 unmodelled-commercial-storage gap. All gaps explained in `claude/HANDOVER.md`.

---

## 4. Schema (oil_network)

### 4.1 Core tables

| Table | Purpose |
|---|---|
| `assets` | identity + JSONB attributes (configuration / resolution_hierarchy); `kind ∈ {physical, abstract}` |
| `nodes` | attaches each asset to a `graph_id` |
| `variables` | one row per (variable_type × commodity × node_id × related_node_id); PK = variable_id |
| `variable_assignments` | explicit overrides of node-type defaults; binds variable to TS or formula; CHECK `num_nonnulls(timeseries_id, formula) = 1` |
| `node_type_default_formulas` | structural defaults per (node_type × variable_type) |
| `scenario_node_role` | balance vs constraint role per scenario |
| `commodities` | commodity registry incl. sweet/sour, density class, API range, sulfur range, region, typical basin |
| `commodity_hierarchy` | parent-child grade tree (`crude → wti → wti_midland`, etc.) |
| `timeseries` | catalogued TS (one row per series_id) |
| `timeseries_data` | vintaged facts (`(series_id, observation_date, saved_date)`) |
| `scenarios` | scenario registry; one row per scenario_id, bound to a graph_id |
| `scenario_resolver_runs` | audit log of every resolver invocation |
| `scenario_resolved_values` | resolver output: one row per (scenario, variable, date); PK = `(scenario_id, variable_id, observation_date)` |
| `scenario_html_artefacts` | audit log of every HTML render |
| `asset_capacities` | physical capacity per (asset, commodity, kind) — scenario-agnostic |
| `variable_constraints` | scenario overlays (deratings, commercial limits) — populated as needed |

### 4.2 Materialised views (12, built by `code/migrations/thirteenth_pass_views.py` + later passes)

| Layer | View | Reads from |
|---|---|---|
| L2a | `v_formula_input_links` | base |
| L2b | `v_aggregation_edges` | L2a + variables |
| L2c | `v_flow_edges` | variables |
| L3a | `v_partition_tree` | L2b |
| L3b | `v_node_status` | base |
| L3c | `v_partition_sums` | resolved + L3a |
| L3d | `v_partition_intra_flows` | flows |
| L4a | `v_node_balance_check` | resolved + L2c |
| L4b | `v_aggregation_consistency` | L3a + resolved |
| L4c | `v_inventory_changes` | resolved |
| L4d | `v_aggregate_balance` | L3a + L4a |
| L4e | `v_node_pcisob` | resolved + metadata |

### 4.3 Read-side views (regular)

- `v_effective_assignments` — overlay-aware variable_assignments (latest effective_from per variable per scenario).
- `v_effective_constraints` — joins `asset_capacities` + `variable_constraints` (overlay wins). One row per (scenario, variable, capacity_kind, effective_from).
- `v_resolution_anomalies` — flags long LOCF runs + negative derived values; advisory.
- `v_commodity_ancestors` — recursive ancestor chain for any commodity in `commodity_hierarchy`.
- `v_node_production_sources` — per-node production attribution (legacy).

---

## 5. Code layout (`Thesis/clean/code/`)

### 5.1 Active files (45 `.py` + 10 `.ipynb` in `code/`, plus 23 one-shot scripts in `code/migrations/`)

**Library / engine:**
- `paths.py` — single source of truth for filesystem locations.
- `network_graph.py` — NetworkGraph engine (read API consumed by renderers).
- `resolve_scenario.py` — the primary resolver (topo + mirror). Auto-refreshes L4 views after each run.
- `recursive_resolver.py` — alternative fixed-point resolver. Output-equivalent to legacy.
- `compare_resolvers.py` — diffs the two resolvers row-by-row and dispatch-stat-by-dispatch-stat.
- `refresh_views.py` — `--structural` / `--analytic` mat-view refresh.
- `render_utils.py` — metadata beacons + HTML audit recording.
- `pdf_utils.py` — shared reportlab helpers.

**Orchestrators (notebooks):**
- `initialize_oil_network.ipynb` — master, 4 stages.
- `initialize_oil_logistics_network.ipynb` — stage 1 (schema + asset graph).
- `build_oil_network.ipynb` — schema DDL.
- `load_asset_graph.ipynb` — loads `config/asset_graph.json`.
- `initialize_oil_network_metadata.ipynb` — stage 2.
- `initialize_oil_network_data_loader.ipynb` — stage 3 (EIA staging).
- `load_eia.ipynb` — EIA API fetcher.
- `initialize_oil_network_assignments.ipynb` — stage 4 (38 steps).
- `assign_eia.ipynb` — TS bindings.
- `assign_formulas.ipynb` — formula-bound assignments.

**Migrations (called by stage-4 orchestrator, all in `code/migrations/`):**
23 one-shot scripts encoding the chronological build-up of the schema. See `claude/NOTEBOOKS.md` for the full chain. Each is idempotent; the orchestrator runs all 23 on every clean rebuild.

**Audits:**
- `audit_capacity_violations.py` — resolved values vs declared capacities (advisory).
- `audit_resolution_anomalies.py` — flags long LOCF runs + negative derived.

**HTML renderers:**
- `regenerate_htmls.py` — orchestrator (`--force` / `--list` / `--views ...`).
- `make_partition_map.py`, `make_node_neighbors_map.py` — NetworkGraph-engine direct.
- `make_balance_resolver_ui.py`, `make_hierarchy_resolver_ui.py`, `make_map_resolver_ui.py` — resolver-driven (template providers: `make_balance_ui.py`, `make_hierarchy_explorer.py`, `make_map.py`).

**PDF generators (write to `outputs/docs/`):**
- `pdf_design_principles.py`, `pdf_resolver_walkthrough.py`, `pdf_graph_construction.py`, `pdf_scenario_construction.py`, `pdf_design_principles_new.py`.

**Thesis-specific (stage 1 additions):**
- `build_fig_3_1.py` — renders the Section 3.1 supply-chain schematic.
- `build_thesis_v37.py` — wraps the v36 → v37 diff (fig 3.1 swap + future-work paragraph).

### 5.2 Archival (`code/old/`)

Pre-Postgres notebooks, retired audits, legacy renderers. Nothing active reads here.

---

## 6. JSON read audit (per Pedro's "no JSON-as-runtime-data" rule)

The codebase reads exactly one JSON file: `config/asset_graph.json`, by `load_asset_graph.ipynb`, for DB seeding only. `render_utils.py` uses `json.dumps`/`loads` for the HTML metadata beacon (string in HTML, not a file read). No other JSON-as-runtime-data anywhere.

---

## 7. Capacity layer

Two complementary tables + a join view:

- **`asset_capacities`** — physical reality of each asset, per commodity. Scenario-agnostic. `capacity_kind ∈ {throughput, storage, consumption, production}`. Seeded from `assets.attributes->configuration` JSONB.
- **`variable_constraints`** — scenario-specific overlays. Empty by default. Populated when a scenario actually needs to override (deratings, commercial limits, what-if).
- **`v_effective_constraints`** — read-side join. For each (scenario, variable), takes overlay row if present, falls back to asset capacity. `layer` column tags the source.

### 7.1 Coverage today (215 capacity rows)

| capacity_kind | unit | rows | distinct assets |
|---|---|---:|---:|
| consumption | kbd | 115 | 115 |
| production | kbd | 20 | 20 |
| storage | mbbl | 25 | 25 |
| throughput | kbd | 55 | 24 |

(Throughput has 55 rows across 24 distinct pipeline assets because 11 pipelines have `capacity_history` entries spanning multiple `effective_from` dates: DAPL 470→570→750 kbpd, Capline 1,200→0→200 kbpd at reversal, Seaway 850→950 kbpd, etc.)

### 7.2 Audit policy

`audit_capacity_violations.py` runs after `resolve_scenario.py` in the orchestrator. It compares every resolved value against the as-of bound from `v_effective_constraints` and emits warnings for any breach. The audit is **advisory** — observed values outside declared capacity are flagged but do not block the orchestrator.

---

## 8. Crude-grade registry (stage 1 addition)

Seeded by `code/migrations/build_us_crude_grade_map.py`. Extends `oil_network.commodities` with eight classification columns (sweet_sour, density_class, api_gravity_min/max, sulfur_pct_min/max, region, typical_basin) and creates `oil_network.commodity_hierarchy` for parent → child grade relationships, plus the convenience view `v_commodity_ancestors`.

19 grades populated: `crude` (root); 9 light sweet (`wti` + its 3 delivery points, `bakken_light`, `eagle_ford_light`, `eagle_ford_condensate`, `lls`, `niobrara_sweet`, `oklahoma_sweet`); 5 medium sour (`mars`, `poseidon`, `thunder_horse`, `southern_green_canyon`, `ans`); 3 California heavy (`california_heavy`, `kern_heavy`, `midway_sunset`).

18 parent-child edges: 13 directly under `crude`; 3 under `wti`; 2 under `california_heavy`.

The resolver continues to treat `commodity = 'crude'` as a single dimension. Per-grade propagation is the next concrete deliverable beyond stage 1.

---

## 9. Outstanding work in priority order

1. **Build `v_balancing_item_check`** for Chapter 5 Claim 4 — isolates `closure-B` vs `observed-B` deltas as the magnitude of intra-USA latent flow.
2. **Add `starter_basin` scenario** — basin-primary partition for cross-scenario consistency validation (Chapter 5 Claim 1).
3. **Per-grade resolver propagation** — the registry exists; wire commodity through `formula_inputs` so per-grade aggregates sum cleanly.
4. **LP exporter implementation** — consumes `v_effective_constraints` + latents; documented in Ch 8.
5. **GraphML / PyG export for forecasting** — natural extension once Ch 10 GNN section is operational.
6. **Vessel layer** — specified in Section 3.1.3 but not populated in the starter.

---

## 10. Things to remember

- Pedro is building this for production use at an asset manager. Auditability, provenance, and forward-compatibility are practical requirements, not academic ones.
- The thesis framing (representation + consistency) and the production framing (LP + grade decomposition + forecasting) should not conflict. Both are served by the same rigorous design.
- The clean rebuild is now load-bearing: any time someone resets the schema, the orchestrator restores the full 251-node state without manual intervention. Test it occasionally to keep it that way.
- New capacity data lands in `assets.attributes->configuration` JSONB first (the seed), then `populate_asset_capacities.py` materialises it into `asset_capacities`. Don't write to `asset_capacities` by hand; write to the JSONB seed and re-run the populator. This keeps `asset_graph.json` as the single source of truth for physical-world facts.
- **Stage 1 marker (2026-05-18):** code is frozen. Future work resumes on a new branch when Pedro is ready; the `stage_1_complete` tag is the return point. The DB dump at `Oil Network Project/snapshots/oil_network_stage_1_complete.dump` rehydrates the full state without re-running the EIA staging step.
