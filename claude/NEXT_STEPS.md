# NEXT_STEPS.md

The immediate action items. Work through these in order.

## Step 0: Resume on a fresh / home machine

Already covered at the top of [HANDOVER.md](HANDOVER.md). In short:

```bash
git pull origin main
# Make sure Postgres + venv are set up, then:
.venv/Scripts/jupyter-nbconvert.exe --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=600 \
    Thesis/Code/initialize_oil_logistics_network.ipynb
```

Open the notebook to confirm the inventory and plots match.

## Step 1: build the `variable_assignments` loader — **DONE for production-side (2026-05-06)**

The schema's `variable_assignments` table now holds **123 production-side rows** under scenario `starter_us_crude_2015_2025`. Driven by:

- [load_eia.ipynb](load_eia.ipynb) — pulls EIA staging into `oil_network_data_loader`.
- [assign_eia.ipynb](assign_eia.ipynb) — registers 26 EIA series in `oil_network.timeseries`, copies vintaged facts (kbd-scaled) to `timeseries_data`, writes 11 TS-bound `variable_assignments`.
- [assign_formulas.ipynb](assign_formulas.ipynb) — writes 112 formula-bound rows (4 arithmetic + 35 rollups + 1 sum-outflows + 8 single-outflow + 43 zeros + 21 latents).
- [initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) — orchestrator.

**Remaining for variable_assignments**: refineries (24 nodes), exports (7), imports (4), hubs (10), pipelines (24), gathering (5), origins (6), SPR (4 sites). Same pattern: extend the mapping list in `assign_eia.ipynb` (or add per-domain `assign_*.ipynb` notebooks) and re-run the orchestrator.

**Trigger bug fixed (2026-05-06).** Both trigger functions in [build_oil_network.ipynb](build_oil_network.ipynb) now schema-qualify their internal SELECTs (`oil_network.scenarios`, `oil_network.nodes`, etc.), so they work regardless of the caller's `search_path`. The `connect_args={"options": "-csearch_path=oil_network,public"}` workaround in `assign_eia.ipynb` and `assign_formulas.ipynb` is still in place as defence-in-depth but no longer required.

---

### Historical reference (kept for context)

The schema's `variable_assignments` table is empty. It binds each `(variable_id, scenario_id, effective_from)` to either a `timeseries_id` (an observed series) or a `formula` (a derived expression). The full mapping for the production side is already worked out in [production_map.md](production_map.md) — the loader's job is to translate that map into rows.

> **Production-side mapping**: see [production_map.md](production_map.md) for the complete EIA → variable binding (TS-bound observed, derived formulas, latent, and zero) for all 14 physical production nodes + 9 aggregates + Canada. This was finalised 2026-05-05 alongside the `add_state_residuals.py` patch that added `texas_other` and `montana_other`.

### 1.1 Recommended approach: option (a) — eager bind

For every variable in the database, write an assignment row before EIA data lands:

- **Authoritative production nodes (basin aggregates, GoM, AK, rest_of_l48, SPR sites):** `formula = NULL`, `timeseries_id = <placeholder>`. Overwritten later when the matching EIA series is loaded.
- **Collapsed nodes (state sub-basin, gathering, Cushing operator sub-terminals, Houston export sub-terminals):** `formula` references the parent. e.g. `permian_tx.P = sum_over_children(permian.P)` if collapsed; in practice for collapsed nodes it's typically `0` for variables that don't apply at that resolution.
- **Derived aggregates (refining-centre views, PADD production views, SPR total):** `formula = sum_over_children(<variable_type>)`.
- **Refineries (named + residuals):** `consumption` = observed (placeholder TS); other variables = formulas (`F_in` = `sum_inflow`, `S` = inventory delta, etc.).
- **Pipelines:** `S` (line-fill) = observed, `inflow`/`outflow` per edge = observed (the relational variables).
- **Export terminals:** `outflow` (loadings) = observed.
- **Import terminals:** `outflow` to refineries = observed (or formula).
- **Hubs:** all variables derived from inflow/outflow + balancing item.
- **Balancing item B:** observed by definition (residual in mass balance — IEA convention).

### 1.2 Implementation

Build a Python script `build_variable_assignments.py` that:

1. Reads `scenarios.attributes->'authoritative_levels'` for the active scenario.
2. Walks the variables table.
3. For each variable, decides: TS-placeholder, formula, or zero, based on (a) the node's `starter_status`, (b) the variable_type, (c) the contract's authoritative level for that subsystem.
4. Inserts into `variable_assignments` with `effective_from = '2015-01-01'`.

### 1.3 Validate

- Total assignments = total variables (every slot has exactly one assignment).
- `SELECT count(*) FROM variable_assignments WHERE timeseries_id IS NULL` → these are the formula-bound variables. Spot-check that their formulas reference only existing variable IDs.
- `SELECT * FROM variable_assignments WHERE node_id IN (SELECT node_id FROM nodes WHERE node_type = 'refinery')` → verify each refinery has 4 non-relational + N relational assignments.

## Step 2: align EIA fetcher with the new schema

**Step 2a — DONE.** Raw EIA pull lives in [load_eia_data.ipynb](load_eia_data.ipynb), writing to a separate `source_eia` schema (kept clean from `oil_network`). 26 datasets / 1,485 series / 144k vintaged rows from 2015-01-01 onwards. Incremental by default (per-dataset `start = max(observation_date) - 180d`); `RESET = True` for a fresh-clone full refetch. Vintage-on-change semantics: `eia_data` PK is `(series_id, observation_date, saved_date)`, new vintage row only when value changes.

**Step 2b — pending.** Loader from `source_eia.eia_data` → `oil_network.timeseries` + `oil_network.timeseries_data`. Needs an **EIA-series-to-asset mapping** (start as JSON in `asset_graph/eia_series_map.json`, migrate to a table later). Mapping examples:
- `MCRFPP21` (PADD 2 production, monthly) → asset `padd2_production_view`, ts_type `production`
- `MCRFPUS1` (US total production) → derived; computed in `oil_network`, not loaded directly
- `MCRRIP31` (PADD 3 refinery input) → asset `padd3_refining_view`, ts_type `consumption`
- `MTTEX_NUS-Z00_MBBLD` (US crude exports, mbd) → split across export terminals via a default formula

Mapping strategy: rule-based first pass on `(duoarea, process)` → `(asset_id, timeseries_type)`, then manual overrides for residuals + collapsed-node series.

## Step 3: write `node_type_default_formulas`

For each (node_type, variable_type) pair, the default formula that fires when no explicit assignment exists. Examples:

| node_type | variable_type | default formula |
|---|---|---|
| pipeline | inventory | `prev(inventory) + sum_inflow - sum_outflow` (line-fill) |
| pipeline | inflow | observed |
| pipeline | outflow | observed |
| storage_terminal | inventory | `prev(inventory) + sum_inflow - sum_outflow` |
| refinery | consumption | observed |
| refinery | inflow | `sum_inflow` |
| refinery | outflow | `0` (refineries don't outflow crude) |
| refinery | inventory | `prev(inventory) + sum_inflow - consumption` |
| export_terminal | outflow | observed |
| state_sub_basin | production | observed (or 0 if collapsed) |
| ... | ... | ... |

~6 variable types × 14 node types = ~84 max, fewer in practice.

## Step 4: formula evaluator runtime

A function that, given `(node_id, commodity, date)`, walks the variable assignments and computes the mass balance, resolving formulas recursively. Outputs:
- The computed value of every variable
- Any mass-balance residual (the balancing item B)
- Any failed lookups (missing TS, undefined formula reference)

Build it incrementally — start with non-relational variables, then add relational, then add temporal (lag and lead).

## Step 5: Chapter 5 validation experiments

Per CLAUDE.md §7, four claims to demonstrate:

1. **Same scenario under three progressively finer coverage contracts; verify consistency.** Run with PADD-aggregate-authoritative, then state-level-authoritative, then operator-level-authoritative for the Cushing subsystem. The total flows at the PADD level should be invariant.
2. **Invariant rejects deliberately double-counted assignments.** Try to insert two `observed_authoritative` rows for the same variable — schema UNIQUE constraint should reject. Compare to a naive schema that wouldn't.
3. **Collapsed state handles mixed-resolution data without mass-balance degradation.** Provide PADD-level totals and operator-level data for a subset; verify the scenario loader correctly promotes/demotes per the contract.
4. **Balancing item captures operationally meaningful signal.** Map B-series spikes against Hurricane Harvey 2017, COVID 2020, SPR 2022 releases, Colonial Pipeline 2021. Plot residuals; demonstrate they are not random noise.

## Step 6 and beyond

Per CLAUDE.md §7: case-study chapter (Chapter 6), discussion + future work (Chapter 7).

---

## Working instructions

- **Idempotent everything.** New patches go through an `add_*.py` script that re-reads `asset_graph.json`, applies changes, writes back, and updates meta counts. Re-running the script is safe.
- **Run the audit scripts.** After any structural change, run `node_audit.py` + `verify_routing_fixes.py` + `mass_balance_check.py` + `coverage_check.py` to catch regressions.
- **Keep the JSON canonical.** The Postgres tables are reproducible from the JSON via `load_asset_graph.ipynb`. Don't write to Postgres directly — go through the JSON.
- **Use the design principles.** When a structural choice is ambiguous, [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) usually resolves it. Principle 2.4 (formula-implies-edge) is the single most important rule.
- **Ask before significant design decisions.** Anything that touches the schema, the contract semantics, or how observed-vs-derived works should be confirmed with Pedro first.
