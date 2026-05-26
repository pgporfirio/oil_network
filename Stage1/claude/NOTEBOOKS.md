# Notebook hierarchy

How the orchestrator notebooks fit together, and what every active script in `code/` does. The top-level master notebook is `initialize_oil_network.ipynb`. A full clean rebuild (`DROP SCHEMA oil_network CASCADE` followed by running the master) takes ~6–8 minutes and produces the 251-node, 1,870-variable, 291,564-row state described in `PROJECT_STATE.md`.

```
initialize_oil_network.ipynb                                      [MASTER]
│
├── 1. initialize_oil_logistics_network.ipynb                     schema + asset load
│   ├── build_oil_network.ipynb                                   CREATE TABLE oil_network.*
│   └── load_asset_graph.ipynb                                    asset_graph.json -> assets / nodes / locations
│
├── 2. initialize_oil_network_metadata.ipynb                      typed metadata layer
│   └── coverage_check.py                                          (legacy — historical coverage stats)
│
├── 3. initialize_oil_network_data_loader.ipynb                   EIA timeseries ingestion
│   └── load_eia.ipynb                                            EIA API -> oil_network.timeseries{_,_data}
│
└── 4. initialize_oil_network_assignments.ipynb                   variable assignments + resolver + audits
    │
    │   ─── Variable assignments (TS or formula) ───────────────────────────
    ├── assign_eia.ipynb                                           TS-bound rows (basin/PADD level production etc.)
    ├── assign_formulas.ipynb                                      formula-bound rows (sum / arithmetic / latent / zero)
    ├── add_aggregation_constituents.py                            cross-check inputs for aggregate variables
    ├── add_node_roles.py                                          role tagging (balance / constraint / auxiliary)
    │
    │   ─── Migration scripts (chronological build-up of structural overrides) ──
    │   ─── All of these live in code/migrations/ — see the subsection below. ──
    ├── migrations/twelfth_pass_cleanup.py                         canonical 'sum' rule, JSONB attribute cleanup
    ├── migrations/thirteenth_pass_views.py                        L2-L4 materialised views (drop-and-recreate)
    ├── init_resolver_tables.py                                    scenario_resolver_runs + scenario_resolved_values DDL
    ├── migrations/sixteenth_pass_cleanup.py                       v_effective_assignments override semantics
    ├── migrations/seventeenth_pass_xstate_membership.py           pipe_bakken_xstate inventory membership
    ├── migrations/eighteenth_pass_constraint_membership.py        7 constraint nodes -> inventory_inputs of natural parents
    ├── migrations/repoint_foreign_supply_to_imports_agg.py        foreign_supply TS authority -> physical aggregate
    ├── migrations/repoint_canadian_corridor_ts.py                 Canadian corridor TS authority -> pipeline nodes
    ├── migrations/add_padd2_canadian_imports_agg.py               padd2 Canadian imports prototype aggregate
    ├── migrations/add_partition_aggregates.py                     generalisation to all PADD aggregates (19 nodes)
    ├── migrations/split_bakken_gathering.py                       ND/MT split + pipe_bakken_xstate connector
    ├── migrations/wire_bakken_xstate_into_inter_padds.py          declare xstate as constituent of inter-PADD aggs
    ├── migrations/twenty_first_pass_spr_under_padd3.py            SPR -> PADD3 inventory (fixes USA double-counting)
    ├── migrations/promote_spr_total_to_balance_role.py            spr_total role: constraint -> balance
    ├── migrations/add_padd_stock_decomposition.py                 per-PADD tank_farms_pipelines + refinery_stocks
    ├── migrations/refactor_jones_act_into_inter_padd_agg.py       Jones Act in-transit on inter_padd_3_to_5_agg
    │
    │   ─── Capacity layer ──────────────────────────────────────────────────
    ├── create_asset_capacities.py                                 asset_capacities DDL
    ├── create_variable_constraints.py                             variable_constraints DDL (scenario-specific overlays)
    ├── populate_asset_capacities.py                               seed from JSONB configuration
    ├── migrations/patch_pipeline_production_capacities.py         current capacities for 19 pipelines + 19 producers
    ├── migrations/patch_pipeline_timeline.py                      pipeline expansions/derating 2015-2025
    ├── create_v_effective_constraints.py                          read-side join asset_capacities + variable_constraints
    ├── create_v_effective_assignments.py                          assignment-level overrides view
    │
    │   ─── Analytic views (L3-L4) ──────────────────────────────────────────
    ├── populate_node_type_defaults.py                             node_type_default_formulas seed
    ├── create_v_partition_sums.py                                 partition sum materialisation
    ├── create_v_resolution_anomalies.py                           long_locf_run / negative_derived flagging
    ├── migrations/patch_v_aggregation_consistency_missing_data.py LEFT JOIN + partial_coverage classification
    ├── migrations/add_aggregation_constituents.py                 cross-check inputs for aggregate variables
    ├── migrations/add_node_roles.py                               role tagging (balance / constraint / auxiliary)
    │
    │   ─── Resolver ────────────────────────────────────────────────────────
    ├── resolve_scenario.py                                        primary resolver (topo sort + mirror post-pass)
    │
    │   ─── Post-resolve capacity fit + audits ──────────────────────────────
    ├── migrations/patch_production_caps_from_peaks.py             raise production caps below 1.05x historical peak
    ├── audit_capacity_violations.py                               non-blocking advisory audit
    └── audit_resolution_anomalies.py                              flags long LOCF runs + negative derived
```

---

## Standalone tools (not in the orchestrator chain)

These can be invoked manually after a clean rebuild.

### Resolver tooling

- **`resolve_scenario.py`** — the primary resolver. Topological sort + mirror post-pass. Writes `scenario_resolved_values` and a `scenario_resolver_runs` audit row.
- **`recursive_resolver.py`** — alternative resolver using the fixed-point design (see `code/resolver.md`). Identical output to `resolve_scenario.py`; the loop reads as a single statement of the algorithm. CLI-compatible (same flags).
- **`compare_resolvers.py`** — runs both back-to-back and diffs rows + dispatch stats. Use this whenever the recursive resolver changes to verify equivalence.

### Renderers

Run via `regenerate_htmls.py` (orchestrator that picks the stale subset based on `scenario_resolver_runs.run_id`):

- **`make_balance_resolver_ui.py`** — per-node P/C/I/O/B/S table; consumes `scenario_resolved_values` directly.
- **`make_hierarchy_resolver_ui.py`** — drill-down tree from roots to physical leaves with node status.
- **`make_map_resolver_ui.py`** — flat geographic map with node tooltips.
- **`make_partition_map.py`** — geographic partition tree explorer.
- **`make_node_neighbors_map.py`** — per-node neighbourhood with subtype / PADD / state grouping.
- `make_balance_ui.py`, `make_hierarchy_explorer.py`, `make_map.py` — template providers (HTML/JS shells consumed by the above).

### PDF generators

- **`pdf_design_principles.py`** — `claude/DESIGN_PRINCIPLES.md` → `outputs/docs/Design_Principles.pdf`.
- **`pdf_scenario_construction.py`** — `claude/SCENARIO_CONSTRUCTION.md` + the five-stage figure → `Scenario_Construction.pdf`.
- **`pdf_resolver_walkthrough.py`** — guided reading of `resolve_scenario.py` → `Resolver_Walkthrough.pdf`.
- **`pdf_graph_construction.py`** — graph-construction reference → `Graph_Construction.pdf`.

### Diagnostics

- **`verify_state.py`** — one-shot consistency check after a clean rebuild. Headline counts, dispatch breakdown, partition closure at 2024-12-01, spot checks, anomaly counts. Always read this output before reporting "the orchestrator passed".

### Shared utilities (imported, not run directly)

- `paths.py` — single source of truth for filesystem locations (`CODE_DIR`, `CONFIG_DIR`, `HTML_DIR`, `DOCS_DIR`, `ASSET_GRAPH_JSON`).
- `network_graph.py` — `NetworkGraph(scenario_id)` engine; loads in ~300 ms from the L4 mat views; exposes `partition_children`, `descendants`, `status`, `value`, `node_balance`, `inflows_to`, `outflows_from` for renderers.
- `refresh_views.py` — `--structural` / `--analytic` mat-view refresh (called by the resolver automatically at the end).
- `render_utils.py` — metadata-beacon injection + `scenario_html_artefacts` audit row writing for renderers.
- `pdf_utils.py` — shared reportlab helpers (used by `pdf_graph_construction.py`; the newer PDF generators use markdown → xhtml2pdf instead).

---

## File-organisation conventions

- **`code/`** — active Python files and notebooks: resolvers, renderers, PDF generators, DDL / view-creation scripts, audits, diagnostics, and the orchestrator notebooks.
- **`code/migrations/`** — one-shot scripts that mutate the database to a specific state. Each file is run exactly once per fresh build, in the order dictated by `initialize_oil_network_assignments.ipynb`'s `ASSIGNERS` list. The numbered "*_pass_*" scripts, the `repoint_*`, `split_*`, `wire_*`, `refactor_*`, `promote_*`, `patch_*`, and `add_*` scripts all live here. They encode the chronological evolution of the schema and can be read as a project journal: each one corresponds to a discrete design decision documented in `HANDOVER.md` and `time_log.md`. The migrations are still active (the orchestrator runs all 21 on every clean rebuild) but are isolated from the day-to-day Python in `code/` so the active surface stays small.
- **`code/old/`** — retired scripts that are no longer run by the orchestrator and not imported by anything active. Kept on disk for historical reference; safe to ignore.
- **`config/`** — `asset_graph.json` (the seed file loaded once by `load_asset_graph.ipynb`).
- **`outputs/html/`** — five canonical HTML explorers + metadata beacons.
- **`outputs/docs/`** — thesis versions, PDFs, diagrams, figures.

Every script that writes a file imports the relevant constant from `paths.py`. To relocate the project, edit `paths.py` once.

### Why `migrations/` is a subdirectory rather than inline

The pass scripts encode the build-up of the schema over time. Once they have run on a fresh database, they should not need to run again (and most are idempotent in case they do). Pulling them out of the main `code/` surface makes it clearer which files are *the framework* (resolvers, renderers, DDL, audits) and which files are *history* (the migrations that brought the database to its current state). Both stay together in the orchestrator chain, but the directory boundary marks the conceptual split.

---

## Fresh-machine bootstrap

```bash
git clone git@github.com:pgporfirio/oil_network_clean.git
cd oil_network_clean/Thesis/clean
# Run setup.ipynb (in the project root, not code/) — it pip-installs
# requirements, captures EIA_API_KEY + Postgres credentials into a
# gitignored .env, provisions the DB role + database, then runs the
# master orchestrator end-to-end.
```

After `setup.ipynb` completes, run `python code/verify_state.py` to confirm the headline numbers match.
