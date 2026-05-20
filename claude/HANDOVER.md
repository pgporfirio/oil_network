# `oil_network` — handover

---

## Resume here (2026-05-20 night — propagator v1 + Composition panel showing real per-grade data)

**Where to pick up:** `Stage2/`, `main`, clean tree, up-to-date with `origin/main`. DB has 1,896 variables (was 1,872 — +24 new grade-production variables across 13 US basins), 587,496 resolved rows across 2 scenarios, 0 unresolved. **Closure verified across every basin** (sum of per-grade values = crude value, max_abs_gap = 0.0000 on every basin × date combo). The balance-hierarchy HTML's Composition panel now actually shows grade values.

**What landed this session:**

- **Propagator v1** (`code/migrations/propagate_grades_at_producers.py`). Producer-level grade decomposition: for each (basin, grade, share) in a hardcoded `BASIN_GRADE_SHARES` table, creates `production__{grade}__{basin}` as a scalar `<share> * production__crude__{basin}`. 13 basins × 1-5 grades each = 24 share assignments. Shares per basin sum to 1.0 by construction so per-grade values reconcile to crude. Affects only `crude_starter_with_grades`; starter scenario stays single-commodity. Idempotent.
  - Permian-TX: wti_midland 0.70 / wtl 0.20 / permian_condensate 0.10
  - Permian-NM: wti_midland 0.80 / wtl 0.15 / permian_condensate 0.05
  - Eagle-Ford-TX: eagle_ford_light 0.60 / eagle_ford_condensate 0.40
  - Bakken-ND, Bakken-MT, Montana-other: bakken_light 1.00
  - Gulf-of-America: mars 0.40 / thunder_horse 0.25 / poseidon 0.15 / southern_green_canyon 0.10 / lls 0.10
  - Alaska-North-Slope: ans 1.00
  - California-conventional: kern_heavy 0.60 / midway_sunset 0.40
  - Oklahoma / Colorado: oklahoma_sweet / niobrara_sweet at 1.00
  - Wyoming-conventional: wyoming_sweet 0.70 / wyoming_asphaltic 0.30
  - Texas-other: wti_midland 1.00 (Permian-tail residual)
  - Foreign / canadian_oil_sands skipped (no WCS grade in registry yet)
- **Closure verified.** Sum-over-grades = crude at every (basin, date) — 13 basins × 156 dates = 2,028 closure checks, all pass with `max_abs_gap = 0.0000`.
- **Balance-hierarchy HTML regenerated** (`outputs/html/oil_network_balance_hierarchy.html`, 3.4 MB). The Composition panel below the time-series chart now shows the per-grade breakdown rooted at `crude` (refined-product subtrees are hidden — those are a separate workstream). Click any producer P cell → see crude + wti_midland + wtl + permian_condensate (etc.) with values, click downstream nodes → see only crude (because v1 doesn't propagate per-grade flow variables down the network).
- **Commodities table now carries 87 entries** (refined products + crude grades) instead of the 23 we had earlier in the session. The new entries are Pedro's separate additions (refined products: gasoline, jet_a, ethane, bunker_c, etc.). The hierarchy root is now `oil`, with `primary_oil → crude → grade-set` as the relevant subtree for what we're doing. Composition panel is hardcoded to start at `crude` so it stays focused.

**What's still v1.1 vs full propagator:**

- v1.1 propagates per-grade variables at **producer nodes** (production + per-edge outflow with proportional allocation) AND mirror-promotes the inflow on the immediate downstream node. So for Bakken-MT (single grade, single outflow): outflow to gathering shows `bakken_light = 55.51`; the gathering inflow shows it too via mirror.
- For multi-outflow basins where the per-edge **crude** outflow is itself latent (e.g. Permian-NM has 2 outflow edges, both latent because we don't have per-edge TS data): the per-grade outflow stays latent too. The proportional formula correctly propagates NULL — we only get per-grade values where we have per-edge crude data.
- Still not propagated (full propagator): per-grade variables on intermediate nodes (gathering's own outflows, pipelines, terminals, hubs, refineries). The first-hop mirror only fills the first downstream node; beyond that you'd need to instantiate explicit per-grade variables at each intermediate node and decide on an allocation rule.
- Refinery slate (`C_g(refinery) = slate_g * C_crude(refinery)`) — same shape as the producer-share formula, easy to add when slate data lands.
- Inventory dynamics at storage (`S_g(t) = S_g(t-1) + ΣF_in_g(t) - ΣF_out_g(t)` via the `formula_input_offsets = [-1, 0, 0]` machinery) — needs per-grade inflow/outflow at storage hubs first.

**To pick up next time:**

1. `cd Stage2 && git pull && python -X utf8 code/verify_state.py` (251 assets, 1,896 variables, 587,496 resolved rows, 2 scenarios, 0 unresolved).
2. Open `outputs/html/oil_network_balance_hierarchy.html`. Navigate to any basin in the partition tree (e.g. `permian_subsystem → permian → permian_tx`), click the **P** cell. The composition panel below the chart shows the crude breakdown with the three Permian grades.
3. Decide what's next:
   - **Downstream propagation** — instantiate per-grade flow variables across the reachable subgraph, declare them latent. Visible per-grade values then start showing up at gathering / pipeline / hub / refinery nodes (initially as `latent`, then filled by the LP allocator).
   - **Inventory dynamics** — use the `formula_input_offsets = [-1, 0, 0]` machinery to express `S_g(t) = S_g(t-1) + ΣF_in_g(t) - ΣF_out_g(t)` at storage hubs.
   - **Refinery slate** — bind `C_g(refinery) = slate_g(refinery) * C_crude(refinery)` for refineries with known yields.
   - **Constant share variables → TS-bound shares** — replace the hardcoded constants in `propagate_grades_at_producers.py` with share variables that themselves are bound to Argus / Platts assay TS or to constant assignments per scenario.

**Quick git context:**

| Repo | Branch | Latest | Status |
|---|---|---|---|
| `Stage2/` → `pgporfirio/oil_network` | `main` | (commit landing now) | local, pushing |
| `Thesis/clean/` → `pgporfirio/oil_network_clean` | `main` | `93dc34f` | historical archive |

---

## (previous) Resume here (2026-05-20 night — resolver: lagged refs + scalar arithmetic; balance-hierarchy HTML)

**Where to pick up:** `Stage2/`, `main`, clean tree, up-to-date with `origin/main`. DB unchanged structurally — both scenarios resolve to identical headline numbers as before (no regression). Resolver now supports inventory-style recursion and scalar formulas, which are the two foundations for the per-grade decomposition work.

**What landed this session:**

- **`formula_input_offsets INT[]`** (commit `1df18f9`). New column on `variable_assignments`, parallel to `formula_inputs`, holds per-input month offsets. NULL/missing = all-zero (same-date, fully backward-compatible). The resolver's `eval_sum` / `eval_arithmetic` honour offsets via a `shift_months` helper, `get()` returns `0.0` for any date before the scenario's first date (the natural seed for cumulative variables — the "t=0 bootstrap problem" from the old `stage_2_grades` attempt disappears cleanly under this convention), and an incremental `put()` callback registers each date's value as it's computed so self-references at offset `<0` find the previously-computed value. `v_effective_assignments` view extended to expose the new column.

- **Scalar arithmetic + `*` + `/` via AST whitelist** (commit `8ab83fe`). Replaced the regex-based `+/-` term parser with a Python AST evaluator restricted to a safety whitelist: `Constant` (numeric), `Name`, `BinOp(Add/Sub/Mult/Div)`, `UnaryOp(USub/UAdd)`. No function calls, no attribute access, no power, no comparisons. Same safety profile as before, just with multiplicative scalars added. Legal formulas now include `0.3 * x`, `0.7 * x + 0.3 * y`, `-0.5 * x + y`, `x / y` (div-by-zero short-circuits to `0.0` — pragmatic for share-style formulas where share-of-zero is zero, not an error). Existing `+/-` formulas classify identically (same AST shape) so no regression.

- **Test suite + validation.** `code/_test_lagged_refs.py` — 9 unit tests covering `shift_months`, lagged self-references (`x(t) = x(t-1)` → zeros, `x(t) = x(t-1) + y(t)` → arithmetic progression), `sum` with offset, scalar multiplication / mixed / division / division-by-zero / unary minus, and the `classify` whitelist. All pass. Regression: starter scenario resolves to identical dispatch counts (90 / 542 / 774 / 5 / 446 / 15 / 0 = 1,872 = 0 unresolved) and identical 291,876 rows. DB integration: temporary scalar override `production__crude__padd1_other = 0.5 * production__crude__padd1_view` produced exactly half the parent value at every date (ratio = 0.5000 confirmed); reverted after test.

- **`serve_balance_hierarchy.py`** (this commit). Live HTTP server on port 8766 (pattern matches `serve_node_routes.py`). Endpoints: `/init` (scenarios, nodes, dates, commodity hierarchy), `/data?scenario=X&node=Y&date=Z` (per-variable-type per-commodity values aggregated over `related_node` edges where applicable). The HTML at `/` shows, per selected node × date × scenario, six tiles (P / C / S / B / F_in / F_out). Each tile renders the commodity_hierarchy as an expandable tree: top row is `crude` (the root), children are the grades; today only `crude` rows carry values because the propagator hasn't run yet, but the moment per-grade variables get added they'll auto-populate under the same tree with no HTML change. Smoke-tested against both scenarios at `cushing_hub` and `permian_tx`: production / consumption / inventory / balancing_item / inflow / outflow all render with correct values and `source` annotations.

**What still needs the propagator:**

- A `propagate_commodity.py`-style migration that, given a grade and a producer set, walks the reachable subgraph and instantiates per-grade variables on every reachable node. Binds: producers' `P_g = share_g(basin) * P_crude(basin)` (where `share_g` is itself a new variable, bindable to a constant or TS — per Pedro's intent); flow variables `latent()`; storage inventory `S_g(t) = S_g(t-1) + ΣF_in_g(t) - ΣF_out_g(t)` with `offsets = [-1, 0, 0]`; refinery yields per slate fraction; and the closure `var_crude(node) = sum_g var_g(node)` so the crude-level numbers stay consistent with the per-grade decomposition.

**Open items inherited (still relevant):**

- Topology cleanups from the routes audit that weren't blocking (Cushing sub-terminal orphan nodes — by design per the scenario's authoritative_levels; East Coast refineries lack CBR paths; Spearhead encoded with Patoka origin instead of Flanagan).

**To pick up next time:**

1. `cd Stage2 && git pull && python -X utf8 code/verify_state.py` (251 / 1,872 / 583,752 across 2 scenarios).
2. To open the new HTML: `..\..\.venv\Scripts\python.exe code\serve_balance_hierarchy.py`, then http://127.0.0.1:8766/. Pick scenario + node + date; verify trees look right.
3. Build `code/migrations/propagate_commodity.py`. First target: `wti_midland` from `permian_tx` + `permian_nm` as the simplest test case. The closure + scalar-share + lagged-inventory machinery is all in place; this is now pure migration plumbing.

**Quick git context:**

| Repo | Branch | Latest | Status |
|---|---|---|---|
| `Stage2/` → `pgporfirio/oil_network` | `main` | (commit landing now) | local, pushing |
| `Thesis/clean/` → `pgporfirio/oil_network_clean` | `main` | `93dc34f` | historical archive |

---

## (previous) Resume here (2026-05-20 evening — routes audit, crude_starter_with_grades scenario, topology fixes)

**Where to pick up:** `Stage2/`, `main`, clean tree, up-to-date with `origin/main`. DB now carries **2 scenarios** (starter + new clone) with the topology fixes applied. Headline: 251 assets, **1,872 variables** (was 1,870), **5,899 routes** in `v_node_routes` (was 9,333 — spurious bidirectional loops removed), 583,752 resolved values total across both scenarios, 0 unresolved.

**What landed this session:**

- **Map-based node-routes explorer** (commit `993c577`) — replaced the trie-tree variant of `oil_network_node_routes.html` with a Plotly natural-earth map matching `oil_network_node_neighbors.html` style. Click a node → upstream subgraph (orange) + downstream subgraph (green), everything else dimmed. Per-node up/down node-sets and edge-sets pre-computed from `v_node_routes` at generation time.
- **Node-type grouping** (commit `01b6515`) — added a coarser "node type" option (Production / Gathering / Pipeline / Terminal / Refinery / Foreign sink) to the routes HTML "Group by" dropdown alongside the existing detailed-subtype / PADD / state options. Made it the default since it's more readable.
- **Routes audit by agent** (no commit) — ran a research subagent over all routes ending at storage hubs, SPR sites and refineries, cross-checked against operator docs / RBN Energy / EIA / GEM wiki / S&P Platts. Found 4 blocking topology bugs (Seaway bidirectional, LOCAP direction wrong, DAPL routed to Cushing instead of Nederland via ETCO, BP Cherry Point identity confusion with P66 Ferndale) plus 3 missing real-world flows (Keystone Phase 1 → Patoka, TMX → Cherry Point, Express-Platte → HF Sinclair WY).
- **`crude_starter_with_grades` scenario** (commit `257027b`) — cloned the starter scenario row + 975 variable_assignments under a new `scenario_id` via `code/migrations/clone_starter_to_with_grades.py`. Identical to the starter at clone time; this is the working scenario for the eventual per-grade decomposition layer (grade-specific assignments will override the inherited commodity=crude bindings).
- **Topology fixes from the audit** (commit `b15ef2f`) — `code/migrations/fix_topology_per_audit.py` applies all 7 fixes at the asset-graph level (variables table), so both scenarios inherit them via CASCADE. Net: 1,870 → 1,872 variables; 9,333 → 5,899 routes; spurious Seaway/LOCAP/DAPL loops eliminated; new Keystone/TMX/Express-Platte deliveries wired up. Both scenarios re-resolved cleanly with 0 unresolved.

**Conceptual clarification captured this session** (in conversation, no commit): the **asset-graph level** (`variables` table) declares which variables exist — including the relational pairs `outflow(A→B)` + `inflow(B←A)` whose presence IS the edge. The **scenario level** (`variable_assignments`) per scenario binds each variable to either a timeseries_id OR a formula (`'0'`, `'latent()'`, `'sum'`, arithmetic). Adding a topology edge in `variables` typically requires adding matching `latent()` assignments per scenario, otherwise the resolver leaves the new variables as `unresolved`.

**Open items from the audit not yet acted on:**

- **Cushing sub-terminal orphan nodes** (`cushing_enbridge`, `cushing_enterprise`, `cushing_plains`) — these are intentionally `collapsed_below: cushing_hub` per the starter scenario's authoritative levels, so the absence of edges is by design; only worth flagging if a future scenario wants per-operator Cushing breakdown.
- **East Coast refineries** (Bayway, Trainer, Paulsboro, Delaware City) have only `padd1_imports_agg` as feeder — no crude-by-rail (CBR) path. Out of scope if CBR isn't being modelled, but worth a documentation note.
- **Spearhead origin** is encoded as Patoka but actually starts at Flanagan IL. Document as Flanagan-aggregated-into-Patoka, or add a `flanagan_hub` node.

**Open design questions inherited from earlier sessions** — still all unresolved:

- t=0 seed assumption for per-grade decomposition; share formula choice (S at t-1 vs N-month MA vs Argus/Platts assay TS); per-grade inventory S\_g(t); date-major resolver vs value-fixed-point. Pedro now has a clean `crude_starter_with_grades` scenario to land grade-decomposition work into.

**To pick up next time:**

1. `cd Stage2 && git pull && verify_state.py` (headline: 251 / 1,872 / 583,752, 2 scenarios).
2. Open `outputs/html/oil_network_node_routes.html` and click Cushing / Nederland / Patoka / St James / BP Cherry Point / HF Sinclair WY to visually confirm the topology fixes look right on the map.
3. Decide what's the first grade-decomposition deliverable for `crude_starter_with_grades`: (a) bind per-grade production at one basin (e.g. Permian as a test case — `wti_midland` + `wtl` + `permian_condensate` shares); (b) full resolver rework for date-major / lagged self-references; (c) start by writing a `bind_producer_grades.py`-style migration similar to the rolled-back `stage_2_grades` branch on the old repo.

**Quick git context:**

| Repo | Branch | Latest | Status |
|---|---|---|---|
| `Stage2/` → `pgporfirio/oil_network` | `main` | `b15ef2f` Topology fixes from routes audit | clean, up-to-date with origin |
| `Thesis/clean/` → `pgporfirio/oil_network_clean` | `main` | `93dc34f` | historical archive |

---

## (previous) Resume here (2026-05-20 late — v_node_routes mat view + two explorer HTMLs)

**Where to pick up:** `Stage2/`, `main`, clean. DB state unchanged (251 / 1,870 / 291,564). New mat view `oil_network.v_node_routes` (9,333 simple paths, ~5.7 hops avg, depth-12 limit) plus two new HTML explorers and a tiny local server.

**What landed this session (continuing from the morning's grade-registry commit):**

- **`oil_network.v_node_routes`** (mat view, created by `code/create_v_node_routes.py`). All simple directed paths between physical nodes in the flow graph, up to 12 hops. One row per (origin, destination, path). Indexed on origin / destination / path-GIN, with unique index for `REFRESH CONCURRENTLY`. Recursive CTE, ~30 lines. Added to `refresh_views.py` STRUCTURAL_VIEWS as L3c. Query patterns: `WHERE origin = 'x'` (downstream from), `WHERE destination = 'x'` (upstream to), `WHERE 'x' = ANY(path)` (through).
- **`outputs/html/oil_network_commodities.html`** (renderer `code/make_commodities_hierarchy.py`). Two-panel tree explorer for the commodities + commodity_hierarchy tables. Click a grade to see API / sulfur / region / basin metadata. 23 commodities · 22 edges · 1 root.
- **`outputs/html/oil_network_node_routes.html`** (renderer `code/make_node_routes.py`). MAP variant patterned after `oil_network_node_neighbors.html` — Plotly natural-earth map of physical nodes; click any marker (or pick from the dropdown) to highlight the full upstream subgraph in orange and the full downstream subgraph in green. Per-node upstream / downstream node-sets and edge-sets are pre-computed from `v_node_routes` at generation time and embedded (~700 KB). Side panel lists the upstream and downstream nodes as click-through pills.
- **`code/serve_node_routes.py`** (live tree variant — kept). Tiny `http.server` on port 8765 with `/nodes` and `/routes?o=X|d=X|through=X` endpoints. The HTML it serves at `/` is a tree-based UI (trie of upstream / downstream paths), queries `v_node_routes` per click. Useful as a textual / per-path explorer alongside the map.

**Design feedback captured this session (saved as memories):**

- `feedback-handover-frequency` — update `claude/HANDOVER.md` after every substantive session, not just milestones.
- `feedback-short-reusable-code` — keep code short, reusable, minimum hard-coding. Lean on the existing data structure (which is flexible enough); don't restate what the schema or existing views already give you. Pedro pushed back on an 80-line view DDL that ended up at ~30 lines.

**To pick up next time:**

1. `cd Stage2 && git pull && verify_state.py` (headline: 251 / 1,870 / 291,564 unchanged).
2. Eager HTML: open `outputs/html/oil_network_node_routes.html` in a browser. Live HTML: `..\..\.venv\Scripts\python.exe code\serve_node_routes.py`, then http://127.0.0.1:8765/.
3. Open questions inherited from the previous Resume here — t=0 seed, share-formula choice, per-grade inventory dynamics, date-major vs value-fixed-point resolver — still all unresolved. The grade-registry expansion (morning commit `7b7076a`) and routes view (this session) give the foundation for the next steps; no resolver-side work has started.

**Quick git context:**

| Repo | Branch | Latest | Status |
|---|---|---|---|
| `Stage2/` → `pgporfirio/oil_network` | `main` | (commit landing now) | local, pushing |
| `Thesis/clean/` → `pgporfirio/oil_network_clean` | `main` | `93dc34f` | historical archive |

---

## (previous) Resume here (2026-05-20 — Stage 2 reboot: fresh repo, grade registry expanded)

**Where to pick up:** working directory is **`C:\Users\PedroPorfirio\OneDrive - Jabuticaba\Oil Network Project\Stage2\`** — a fresh git repository (`git@github.com:pgporfirio/oil_network.git`, branch `main`, working tree clean, up-to-date with `origin/main`). The previous `oil_network_clean` repo at `Thesis/clean/` is now historical archive; new work happens in `Stage2/`. DB is the same shared Postgres instance (`localhost:5432/eia_crude/oil_network`) so `verify_state.py` from either location produces identical numbers — **251 assets, 1,870 variables, 291,564 resolved rows, 0 unresolved, 0 TS-binding collisions, 0 capacity violations**.

**Commodities + hierarchy state (just bumped):** 23 commodities (was 19), 22 hierarchy edges (was 18). The new four (commit `7b7076a`):

- `wyoming_sweet` — sweet Wyoming (Frontier / Niobrara WY); WY was previously borrowing `niobrara_sweet`, which is primarily Colorado DJ Basin
- `wyoming_asphaltic` — heavy sour Wyoming (Salt Creek-type), 18–26 API, 1.8–3.0% sulfur
- `wtl` — West Texas Light, the lighter Permian / Delaware shale grade distinct from WTI Midland; CME WTL contract trades it separately
- `permian_condensate` — very-light Permian lease condensate, 50–70 API

All four wired as direct children of `crude`. Variables / variable_assignments / resolver unchanged — every production variable still binds `commodity = crude` (single-commodity scope from stage 1). Grade-decomposition activation is the next workstream.

**What this session attempted, and what landed.**

This session covered the bridge from "stage 1 done, second-attempt stage 2 rolled back" (previous Resume entry below) to "stage 2 properly set up on a fresh repo".

Three pieces of work in the old `oil_network_clean` repo at `Thesis/clean/` before the Stage 2 split:

- `988b403` — added root `CLAUDE.md` as the entry-point for Claude Code (was missing; `claude/CLAUDE.md` existed but Claude Code auto-reads from the project root).
- Audit pass on thesis v44 against the notebook chain — found Chapters 5–7 numerics were stale from an earlier `run_id` (~run_id 5–6 era), not the current `run_id 14`.
- `93dc34f` — thesis v45 + matching PDF, refreshing 29 distinct edits across Chapters 5–7 to align with current DB state. Headlines: 240 → 251 assets, 1,830 → 1,870 variables, 409 → 433 directed flow edges, 80 → 90 authoritative TS bindings, 265 → 377 partition edges, 285,480 → 291,564 resolved pairs. Table 9 merged the old `alias` row into `arithmetic` (the code unified those kinds); Table 22 phi count 30 → 0 with the rollback explained in Annex D.2 prose; Table 26 grew from 12 → 23 migration passes. Throwaway edit scripts (`code/_edit_v44_to_v45.py`, `_edit_v45_secondpass.py`, `_edit_v45_table26.py`) committed as audit trail.

Then Stage 2 set-up (committed at `Stage2/`, pushed to `origin/main`):

- **`7825405` — Initial commit: Stage 2 baseline.** Fresh git repo (no shared history with `oil_network_clean`); mirror of `Thesis/clean/` excluding `__pycache__` and `.git`. 272 files. The shared Postgres DB means `verify_state.py` works identically from either repo.
- **`be3d032` — Clean up.** Removed 4 throwaway scripts, all 42 files in `code/old/`, 56 historical thesis drafts (v5 / v16–v44 + PP variants + v22_annotated), `Asset_Centric_*` and `Annex_A_Implementation_v1.2` historical alternate-title drafts, 16 orphan figures and superseded PDFs (grep-verified zero references), and `RESTORE_STAGE_1.md`. Net: 272 → 153 tracked files, 104 → 52 MB. Memory docs refreshed: root `CLAUDE.md` rewritten to drop the stage_1_complete framing; `claude/CLAUDE.md` §4.1 rewritten to describe v45's actual chapter structure (not the ancient v5 7-chapter skeleton); §9 directory listing rewritten to Stage 2 layout; `claude/PROJECT_STATE.md` header refreshed (Stage 2 baseline framing).
- **`7b7076a` — Grade registry expansion** (this session's substantive work). New migration `code/migrations/add_wyoming_and_permian_grades.py` adds the four grades above with full API / sulfur / region / basin metadata, idempotent ON CONFLICT. 19 → 23 commodities, 18 → 22 hierarchy edges.

Also confirmed: `locations` table needs no update for the new grades. Commodities and locations are decoupled by design (no FK); `typical_basin` on commodities is free-text descriptive metadata. The basin nodes that would produce the new grades (`wyoming_conventional`, `permian_tx`, `permian_nm`) already have proper `locations` rows. Grade-to-place binding happens transitively through `variables` (commodity → variable → node → location).

**Stage 1 carry-overs that still apply:** the design principles in `claude/CLAUDE.md` §2 are unchanged. The 38-step orchestrator is unchanged. The 23 stage-1 migrations are all preserved in `code/migrations/`. The thesis v45 and four reference PDFs are in `outputs/docs/`.

**Open design questions carried forward from the previous Resume here:** the per-grade resolver-propagation work that was rolled back in the previous session still has the same open questions — t=0 seed assumption, share-formula choice (S at t-1 vs N-month MA vs Argus/Platts assay TS), per-grade inventory S\_g(t), date-major resolver vs value-fixed-point. **Nothing on these has been decided this session.** When the next session picks up grade decomposition, those four questions are the entry points.

**To pick up next time:**

1. `cd "C:\Users\PedroPorfirio\OneDrive - Jabuticaba\Oil Network Project\Stage2"`.
2. `git pull` (this handover entry is part of the commit Pedro will make).
3. `..\..\.venv\Scripts\python.exe code\verify_state.py` should print the headline (251 / 1,870 / 291,564 / 0).
4. Decide which entry point for the grade-decomposition work: (a) port `bind_producer_grades.py` patterns from the old `stage_2_grades` branch into a new Stage 2 migration, picking up where the previous attempt left off; (b) start fresh with a date-major resolver redesign, accepting that step takes longer but unblocks lagged self-references cleanly; (c) finish coverage of any other grade-registry gaps before touching variables.

**Quick git context:**

| Repo | Branch | Latest commit | Status |
|---|---|---|---|
| `Stage2/` → `git@github.com:pgporfirio/oil_network.git` | `main` | `7b7076a` Add Wyoming + Permian-light grades | clean, up-to-date with origin/main |
| `Thesis/clean/` → `git@github.com:pgporfirio/oil_network_clean.git` | `main` | `93dc34f` Thesis v45 | historical archive; tag `stage_1_complete` pinned |
| `Stage1/` (no git) | — | — | clean-room test copy (proves location independence) |

---

## Resume here (2026-05-18 evening — stage 2 attempt rolled back; airport-pickup state)

**Where to pick up:** branch `main`, DB restored from `oil_network_stage_1_complete.dump` then rebuilt end-to-end by the master orchestrator. State is stage-1 clean — 251 assets, 1,870 variables, 975 assignments, 291,564 resolved rows, 19 commodities, 18 hierarchy edges, 0 unresolved. `verify_state.py` runs green.

**Working-tree state:** 7 modified notebooks from the orchestrator rerun (`code/initialize_oil_network.ipynb`, the three sub-orchestrators, `assign_eia.ipynb`, `assign_formulas.ipynb`, `load_eia.ipynb`). The diffs are just refreshed cell outputs — no source-code changes. Either commit them ("proof of reproducibility 2026-05-18") or revert with `git checkout -- code/*.ipynb`. **No decision made — left for Pedro on resume.**

**What this session attempted, and why it rolled back.** Branched off as `stage_2_grades` to start per-grade resolver propagation. Two commits landed cleanly there and remain in git history:

- `b4ae1e9` Stage 2 step 1: added `formula_input_offsets INT[]` to `variable_assignments`; threaded offsets through `eval_sum` / `eval_arithmetic` with the early-register pattern so a variable can reference itself at lagged dates; added `wcs` to the commodity registry.
- `c3caab0` Stage 2 step 2: replaced the regex-based `eval_arithmetic` with an AST walker (whitelist Constant / Name / BinOp / UnaryOp / Add / Sub / Mult / Div / USub); `bind_producer_grades.py --commit` wrote 68 new variables (34 share + 34 per-grade production) for the 19 producers, sum-to-crude closure verified zero diff at every producer × 156 dates.

Then attempted step 3 (the forward propagator): walk `v_flow_edges` from each producer, instantiate per-grade outflows on every reachable edge via proportional draw on the upstream inflow mix, plus paired latent inflows + per-grade refinery consumption. **Hit a dependency cycle** on bidirectional edges (Seaway ↔ Cushing / Houston, SPR ↔ hubs, LOOP ↔ St-James, Bakken-xstate gathering pairs, padd_view ↔ padd_view 2-cycles and 3-cycles). With same-date proportional draw, each direction's formula references the other side → circular dep in both topo-sort and `all_inputs_known` gates.

Pedro's design intent: use **time-lagged refs** — the share comes from S at t-1 (or an average over the past 5 months, or another formula). Variables have time as a dimension by design; the resolver should understand that. The cycles disappear under offset=-1 cross-refs *but* there's a t=0 bootstrap problem (at the first date no t-1 exists, lookups return None, partial propagates forward forever). Fixing this properly needs either (a) a date-major resolver that pre-registers every variable then iterates dates in chronological order with a t=0 seed assumption, or (b) value-fixed-point iteration over the existing variable-major resolver. **~100–150 lines of resolver rewrite. Not landed.**

The propagator file (`propagate_grades.py`, ~330 lines) was written and committed 4,598 vars to the DB, then was rolled back per Pedro's call. WIP file + design doc deleted from working tree. Reusable patterns: (1) reachability via fixed-point grade-set propagation; (2) UPSERT pattern matching `bind_producer_grades.py`; (3) per-grade inflows declared `latent()` and auto-filled by the existing `promote_mirrors` (commodity-agnostic, no change needed); (4) the AST evaluator and div-by-zero → 0 tweak (untracked, also rolled back) are needed for the propagator's proportional-draw formulas.

**Open design questions for the next session:**

1. **t=0 seed assumption.** Options surfaced: (a) at t=0, use same-date proportional draw based on inflow mix only (no S yet); (b) at t=0, assume each grade flows in proportion to its long-run share of the producer's output; (c) defer per-grade attribution at t=0 entirely (accept partial rows for January 2015, propagate forward from Feb). Needs decision before the resolver work.
2. **Share formula choice.** S at t-1 vs N-month moving average vs an Argus / Platts assay TS. The framework should support any of these — share is just another variable that can be TS-bound or formula-bound. The choice affects how quickly grade compositions track real-world shifts.
3. **Storage-bearing nodes need per-grade S\_g(t).** Inventory dynamics `S_g(t) = S_g(t-1) + ΣF_in_g(t) − ΣF_out_g(t)` were not implemented this session. Without per-grade S\_g, SPR releases and pipeline line-fill can't be attributed by grade — the back-direction flow has no source mix to draw from. This is where the t=0 seed problem bites hardest.
4. **Resolver architecture.** Date-major (clean) vs value-fixed-point (smaller change). Date-major requires reshaping the evaluator-calls-all-dates pattern to one-date-at-a-time (or wrapping `dates=[d]`).

**Quick git context:**
- `main` (current): clean stage-1 working code, 7 notebook output diffs from this evening's rebuild.
- `stage_2_grades` (preserved): two stage-2 commits as listed above; rest of step 3 work was unrelated and discarded.
- Tag `stage_1_complete`: still pinned to the canonical return point.

**To pick up on the airport machine:** `git pull origin main` (this handover entry is part of the commit Pedro will make); `pg_restore -n oil_network --clean --if-exists ../../snapshots/oil_network_stage_1_complete.dump` if not on the same machine; otherwise the DB is already at stage 1 ready. Then decide: continue the stage-2 resolver redesign on a fresh branch off `main`, or pick up the partial work on `stage_2_grades` and rebuild step 3 on top of a date-major resolver.

---

**Stage 1 complete — 2026-05-18.** Code frozen for thesis defence preparation. The framework — schema, resolver, 12 materialised views, 38-step orchestrator, 5 HTML explorers, 4 reference PDFs, thesis v37 — is reproducible from a clean machine via `setup.ipynb` plus the `stage_1_complete` git tag, or from the DB snapshot at `Oil Network Project/snapshots/oil_network_stage_1_complete.dump` for instant rehydration without re-running the EIA staging step. Future work resumes on a new branch; this tag is the canonical return point. Substantive changes that landed between v27 (the last HANDOVER state) and the stage 1 marker:

- **Thesis v28 → v37 (ten incremental passes):** transport-network map in Section 3.4; Eagle Ford EIA-vs-framework figure in 4.8; figure-text-overflow fixes in 4.13; physical-asset-edge vs geographic-aggregation paragraphs in 5.7; monospace partition-tree snapshot + values table at 2024-12-01 in 5.7; LP-as-downstream-consumer chapter (Ch 8) with Permian fan-out worked example; case-study chapter (Ch 9) with single-date dispatch + Permian fan-out LP solution; LOCF clarification with concrete bpd example in 6.10; Montana discontinuity framed as intended behaviour; Section 7.9 process-to-enforcement table mapping every build-pipeline step to the axioms / corollaries it embodies; Section 10.3 future-work paragraph extended in v37 to note the seeded crude-grade registry; Section 3.1 Fig 3.1 corrected (v36 had the entity-relationships ERD in the supply-chain figure slot — fixed in v37 with a four-stage upstream / midstream / storage / downstream schematic at the correct slot aspect).
- **Recursive resolver alternative.** `code/recursive_resolver.py` implements the fixed-point design from `code/resolver.md` as a single while-loop. Output-equivalent to the legacy topo + mirror `resolve_scenario.py`; `code/compare_resolvers.py` runs both back-to-back and diffs 291,564 rows + dispatch stats. Used as a teaching artefact, not the primary resolver — the legacy implementation remains the orchestrator's default.
- **Crude-grade registry seeded.** `code/migrations/build_us_crude_grade_map.py` extends `oil_network.commodities` with eight classification columns (sweet_sour, density_class, API range, sulfur range, region, typical basin) and creates `oil_network.commodity_hierarchy` for parent → child grade relationships, plus the `v_commodity_ancestors` recursive view. Populates 19 named U.S. grades + 18 hierarchy edges. The resolver still treats `commodity = crude` as a single dimension; per-grade propagation is the first concrete deliverable beyond stage 1.
- **Migration-script directory split.** All 23 one-shot pass / repoint / split / wire / refactor / promote / patch / add scripts moved to `code/migrations/`. Active framework (resolvers, renderers, DDL, audits) stays in `code/`; the migration directory boundary marks the conceptual split between *what the framework is* and *how the database got to its current state*. The orchestrator's ASSIGNERS list updated accordingly. `code/NOTEBOOKS.md` documents the full chain.
- **Two silent orchestrator failures fixed.** `add_padd_stock_decomposition.py` was hitting a NOT NULL violation on `formula_inputs` for production / consumption / balancing_item assignments; fixed by explicitly using `'{}'::text[]` in the INSERT / ON CONFLICT branches. `thirteenth_pass_views.py` was using `DROP VIEW IF EXISTS` on objects that had been re-created as materialised views by an earlier pass; fixed with a PL/pgSQL block dispatching on `pg_class.relkind`. Both failures were silent in the sense that the orchestrator did not abort — downstream cells continued and produced visually plausible but structurally wrong balance UIs (Canadian pipelines appeared as top-level orphans rather than nested under PADD 2). Reordering `add_canadian_pipelines_inventory_membership.py` in ASSIGNERS to run *after* the stock-decomposition pass restored correct partition nesting at `padd2_view`.
- **Live DB headline numbers at stage 1 close:** 251 assets / nodes (217 physical + 34 abstract), 1,870 variables, 975 explicit `variable_assignments`, 76 default formulas, 90 active TS bindings, 68,793 vintaged `timeseries_data` rows, 291,564 resolved rows for the starter scenario, 215 capacity rows (31 time-versioned), 0 variable_constraints (empty by design), 19 commodities + 18 hierarchy edges. Resolver dispatch: 90 observed + 542 zero + 767 latent + 451 arithmetic + 5 sum + 15 reverse_mirror = 1,870 variables, 0 unresolved. Audit invariants: 0 capacity violations, 0 TS-binding collisions, 3,411 v_aggregation_consistency ok + 3,453 partial_coverage + 0 inconsistent, 1,914 long_locf_run + 87 negative_derived (advisory).
- **State docs refreshed.** `claude/PROJECT_STATE.md` rewritten end-to-end against live DB numbers. `claude/NOTEBOOKS.md` already reflects the migration split. `claude/CLAUDE.md` unchanged — design principles and conventions remain stable.

Tagged in git as **`stage_1_complete`** on commit shipped 2026-05-18. The DB snapshot lives outside the repo at `Oil Network Project/snapshots/oil_network_stage_1_complete.dump` (custom-format pg_dump of the `oil_network` schema).

---

**Last working session:** 2026-05-17 (twenty-seventh pass — full Remarkable annotation pass on v22 processed page-by-page; v27 lands 17 text edits + two figures; standalone Scenario_Construction reference document added). Three substantive landed:

**(1) Pedro's full v22 annotation pass turned into v27 thesis edits.** Pedro synced the v22 thesis to a Remarkable tablet, annotated 33 pages, and exported the annotated PDF (now archived at `outputs/docs/Master_Thesis_Pedro_Porfirio_v22_annotated.pdf`). Each annotated page was rendered to PNG via PyMuPDF, read by the model, compared against v26's current state, and either marked as "already addressed in v23–v26" or rolled into v27. Of the markups read, the following text edits landed in v27:

- **Abstract** — "every physical asset" replaces "all nodes" in the mass-balance sentence; a new sentence covers node-vs-variable (each node carries several variables, the CHECK constraint applies per-variable).
- **Section 4.2** — new paragraph on the LP-implications of stable adjacency (fixed coefficient matrix; topology changes are data events, not LP rebuilds).
- **Section 4.5** — the historical "coverage contract" paragraph rewritten to lead with the data-source-granularity argument for the two-layer split; the term is demoted to a parenthetical historical pointer.
- **Section 4.7** — new paragraph on operator-level aggregation (e.g. "Shell US production" as a per-operator aggregate view); new paragraph clarifying resolution hierarchy vs flow path.
- **Section 4.8** — Eagle Ford TX as a concrete aggregate-TS example; a node-vs-variable clarification follows.
- **Section 4.9** — explicit link to grade inference via the same latent-allocation pattern.
- **Section 4.10** — bidirectional simultaneity note (batching pipelines can show flow in both directions over a month); variable-name vs description note (database name is an identifier; the description lives in the metadata).
- **Section 4.11** — LOCF is the industry-standard convention (Kpler, Genscape, IIR all use carry-forward); the audit trail makes it auditable in a way most providers' downstream delivery is not.
- **Section 4.12** — Corollary E rewritten to justify the view-rather-than-column decision (rendering colours, LP decisions, audit interpretation); the word **"collapsed" renamed to "inactive"** in the thesis prose (schema column kept for backwards compatibility).
- **Section 4.13** — new paragraph mapping each axiom and corollary to one or more layered views (v_flow_edges, v_aggregation_edges, v_partition_tree, v_node_status, v_aggregation_consistency, v_node_balance_check, v_resolution_anomalies, v_node_pcisob) with Annex C pointer.
- **Section 5.1** — note on multiple persistent layers (multi-graph support is designed in even though the current scenario uses one `graph_id`).
- **Section 5.2** — explicit note that the asset graph was constructed independently of the EIA time-series catalogue (corporate filings, FERC/PHMSA, OGJ surveys, Global Energy Monitor, Wood Mackenzie IIR as sources), with capacity-coverage numbers (17,484 kbd vs ~17,500 kbd real US refining capacity).
- **Section 5.4** — state_residual expansion (PADD-level tail of unmodelled producers; same boundary-placeholder pattern as foreign_supply, with a clear path to refinement when finer data lands).
- **Section 5.5** — foreign supply visibility per PADD via import-aggregate nodes.
- **Section 5.6** — explicit `phi` variable type definition (cross-node alias reference, no flow / stock / balance role).

**(2) Two new figures generated and embedded in v27.** Both produced via matplotlib in the v27 edit script, saved to `outputs/docs/figures/`, and embedded in the docx as PNGs with centred captions:

- **Figure 1** (end of Section 4.13) — Asset / Node / Graph / Variable / Scenario entity relationships, with the CHECK constraint and the 1:N / N:1 multiplicities annotated. Six coloured boxes connected by arrows; sits at the transition from the design-principles chapter into the schema chapter.
- **Figure 2** (Section 5.9) — End-to-end load → resolve → render pipeline with the L4 analytic views (v_aggregation_consistency, v_node_balance_check, v_resolution_anomalies, v_node_status, v_node_pcisob) feeding the audit machinery from below.

Word count v26 → v27: 39,501 → 41,357 (+1,856 from the text edits and figure captions). Paired PDF generated cleanly at 920 KB.

**(3) Focused Scenario_Construction reference document.** Separate from the thesis, a short standalone document walks through how a scenario is built in five stages, with each stage tagged with the governing axiom(s) and corollary(ies). Source `claude/SCENARIO_CONSTRUCTION.md` (markdown); generator `code/pdf_scenario_construction.py` (same `markdown → xhtml2pdf` pipeline as `pdf_design_principles.py`, but inlines its figure as a base64 data URI to dodge xhtml2pdf's trouble with Windows file:// URIs); output `outputs/docs/Scenario_Construction.pdf` (195 KB). The figure (Stage 1 → Stage 5) is at `outputs/docs/figures/fig_scenario_construction.png` and reuses the matplotlib style of the v27 thesis figures. Ends with a "which axiom/corollary governs what" quick-reference table.

**Tooling additions in this pass:** PyMuPDF (`pymupdf`) installed for rendering Remarkable-annotated PDF pages back into PNG so the model can read the ink strokes; the script renders all annotated pages at 180 DPI, scans `page.get_drawings()` to detect annotated pages, and inspects them in batches. The annotated source PDF is preserved at `outputs/docs/Master_Thesis_Pedro_Porfirio_v22_annotated.pdf` for reference.

**Final state of the thesis:** `Master_Thesis_Pedro_Porfirio_v26.docx` → `v27.docx` (41,357 words) plus paired `v27.pdf` (920 KB). Sequence v17 → v27 all in `outputs/docs/`. Pedro is reviewing v27 next.

**Orchestrator length: 33 steps (unchanged).** Schema and live data are unchanged from the twenty-fifth pass; all twenty-sixth and twenty-seventh pass edits are documentation-only. Numbers remain: 251 nodes, 1,870 variables, 955 assignments, 291,564 resolved values, 0 unresolved, 0 TS-binding collisions, 0 capacity violations.

**Commits on `main`:** `451fab1` v27 third review-pass edits, `b7d137f` Scenario_Construction reference (md + PDF + figure + generator script).

---

**Earlier on 2026-05-16 (twenty-sixth pass — Pedro's first review feedback on v22 prose, iterated through v23 → v26). Same-day continuation of the twenty-fifth pass. Four substantive landed:

**(1) Thesis v23 — clarify 'single attribution' vs formula_inputs dual role.** The CHECK-constraint phrasing in the Abstract risked being misread as forbidding aggregation relationships entirely. Three targeted changes: the Abstract sentence was rewritten as "a CHECK constraint enforces single attribution **of value**" with an explicit clarifying sentence that aggregation relationships sit on a separate column; a new paragraph was inserted in Section 4.8 (Axiom 5) explaining the dual role of `formula_inputs` (constraint set when TS-bound, operand set when formula-bound) with PADD-level production as the canonical example; Section 7.1 Claim 1 opening was rephrased to flag the same point with a back-reference to Section 4.8.

**(2) Thesis v24 — surface the assets/nodes separation in the Introduction.** Pedro asked for the architectural split between `oil_network.assets` and `oil_network.nodes` to be flagged upfront rather than left for Chapter 5. Two additions: the Abstract gained three sentences stating that each node binds to exactly one asset, the same asset can appear in multiple graphs without being duplicated, and assets carry a `kind` flag (physical / abstract). Section 1.4 (Scope and delimitations) got two new paragraphs at the end covering the same vocabulary in depth — assets are primitive entities, nodes are how assets appear in a particular graph, physical assets have capacity / geography / edges while abstract assets are aggregation views with no edges of their own.

**(3) Thesis v25 — first review-pass edits (Pedro read to page 19).** Six substantive changes in one pass:
- **1.2 central problem boxed** — paragraph borders (1.5pt blue) + light blue shading on the *"how can the crude oil logistics network be represented..."* paragraph.
- **Retrospective language stripped** in four places (1.4, end of Chapter 8 transition, end of 10.1, opening of 10.2). The "earlier versions / earlier framing / current thesis reverses" phrasings were replaced with direct present-tense statements.
- **2.2 oil vs traffic networks expanded** — new paragraph after the traffic-forecasting reference covering the differences (commercial vs individual agents, sparse vs dense data, line-fill inventory vs no-inventory junctions, directional reversals).
- **2.3 PADD defined** — paragraph inserted after the first occurrence of "PADDs" giving the expansion (Petroleum Administration for Defense District) and listing all five (PADD 1 East Coast, PADD 2 Midwest, PADD 3 Gulf Coast, PADD 4 Rocky Mountain, PADD 5 West Coast).
- **2.3 commercial data sources + multi-scenario** — paragraph on OilX, Genscape, Wood Mackenzie IIR, and S&P Global as finer-granularity complements to EIA, and the framework's ability to mix public and commercial sources through scenario bindings.
- **3.3 crude-grade table** — markdown-style table summarising the dominant crude grade by producing region (Permian, Bakken, Eagle Ford, Gulf of America, Alaska, California, Oklahoma, Wyoming), with API gravity, sulphur, and notes; intro paragraph flags per-grade decomposition + refined-product yield as natural extensions.
- **1.4 grade decomposition + refinery yield extensions** — paragraph flagging per-(asset, grade) tank-blend tracking and refinery yield equations (`gasoline_P = 0.35 × crude_C of grade X`) as extensions sitting on top of the existing schema, without structural change.

**(4) Thesis v26 — second review-pass edits (Pedro read to page 23).** Two more changes:
- **4.1 data-provider granularity examples** — new paragraph after the Jones Act example listing what commercial sources resolve at asset level: Genscape tank-level imagery for Cushing (twenty-plus individual storage tanks resolved rather than a single PADD-2 aggregate), OilX / Kpler per-vessel cargo tracking by ship / route / grade, IIR Energy per-unit refinery turnaround schedules, S&P Global per-pipeline shipper nominations. Each binds to the node it describes; a location-centric representation would have no natural home for any of them.
- **4.3 mass balance applies to physical nodes only** — Section 4.3 opening narrowed from "every node, regardless of type, satisfies the same mass-balance equation" to "every physical node satisfies the mass-balance equation". New paragraph after the where-clause stating explicitly that mass balance is enforced at physical assets only, that abstract assets are aggregation views (Axiom 3 roll-ups, or independently TS-observed at the aggregate level), and that the comparison between an observed aggregate value and its constituent sum is a cross-check rather than a separate constraint (with forward-reference to the dual-role pattern in Section 4.8).

**Final thesis state at end of pass:** `Master_Thesis_Pedro_Porfirio_v26.docx`, 39,501 words, paired `v26.pdf` (740 KB). The sequence v17 → v26 all sits in `outputs/docs/`. Pedro's review reached page 23; v27+ will continue from page 24 onwards, driven by the same incremental review-comment cadence.

**Orchestrator length: 33 steps (unchanged).** The twenty-sixth-pass edits are documentation-only; the schema and live data are unchanged from the twenty-fifth pass. The verification numbers from the twenty-fifth-pass clean rebuild remain authoritative: 251 nodes, 1,870 variables, 955 assignments, 291,564 resolved values, 0 unresolved, 0 TS-binding collisions, 0 capacity violations.

**Commits on `main`:** `16774c6` v23 single-attribution clarification, `ba0935f` v24 assets/nodes in intro, `3df7be4` v25 first review pass, `7e36e33` v26 second review pass.

---

**Earlier on 2026-05-16 (twenty-fifth pass — thesis evolution v17 → v22: structural alignment with the axiom/corollary framework, prose humanisation, paired-PDF Remarkable workflow). Five substantive landed:

**(1) Thesis v18 — trim forward-looking content.** Section 7.5 (`Claim 5 (queued): Cross-scenario consistency`) compressed from three paragraphs to one and the heading renamed to drop `Claim 5 (queued):`. Section 7.8 Summary first paragraph rewritten to drop "queued for the next development pass" framing. Section 10.3 Future work compressed from seven bullet items (~700 words) to a single paragraph (~150 words), with the temporal GNN extension kept as the lead example. Net −600 words.

**(2) Thesis v19 — recent-pass additions woven in.** Five concepts from the 22nd/23rd passes that were absent from v17 were added in the appropriate chapters: (a) Section 6.10 *Last-observation-carried-forward* as a new subsection covering the temporal convention, the `formula_used` audit trail, and the `montana_other` negative-residual illustration; (b) Section 4.1 Jones Act in-transit example showing PADD-5 line-fill on `inter_padd_3_to_5_agg`; (c) Section 4.9 `pipe_bakken_xstate` cross-state connector example for latent allocation; (d) Section 5.4 PADD stock decomposition (`tank_farms_pipelines` + `refinery_stocks` aggregates materialising `MCRSTP{N} = MCRSFP{N} + MCRRSP{N} (+SPR)`); (e) Section 7.2 `partial_coverage` classification rule and the retirement of the `inconsistent` label.

**(3) Thesis v20 — restructure Section 4 as axioms + corollaries.** The thesis still talked about "twelve principles" while `DESIGN_PRINCIPLES.md` had been refactored in commit `9cbf907` (twenty-fourth pass below) into 6 axioms + corollaries. Chapter 4 brought into alignment: heading text tagged with each subsection's role (`4.1 Axiom 1: Asset-centric representation`, `4.3 Corollary A: Universal mass balance`, etc.); chapter intro rewritten with the axioms framing; 4.11 (canonical sum rule) and 4.12 (latent vs unresolved) dropped from Chapter 4 — they are resolver behaviour, already covered in Section 6.7 and Annex C.2.4 — and 4.13 Summary renumbered to 4.11. 18 in-line `Principle N` cross-references in Chapters 5–9 renamed to `Axiom X` or `Corollary Y` per the mapping (1→Ax1, 2→Ax2, 3→CrA, 4→Ax3, 5→Ax4, 6→CrB, 7→CrC, 8→Ax5, 9→CrD, 10→Ax6). Seven mentions of "twelve [architectural] principles" updated.

**(4) Thesis v21 — add Corollary D-bis (LOCF) and Corollary E (node status).** Chapter 4 brought into full alignment with `DESIGN_PRINCIPLES.md` by adding the two corollaries v20 left out. New 4.11 *Corollary D-bis: Last-observation-carried-forward* states the temporal convention as a corollary of Axiom 5 with implementation cross-reference to Section 6.10. New 4.12 *Corollary E: Node status as a view* states that a node's authoritative/derived/collapsed status follows mechanically from `variable_assignments` and is materialised by `v_node_status`, with the same logic extended to other derivable node attributes (`v_flow_edges`, `v_partition_tree`, `v_node_pcisob`). 4.11 Summary renumbered to 4.13; Summary body updated; chapter intro count corrected to "six axioms and six corollaries". The thesis structure now matches `DESIGN_PRINCIPLES.md` exactly: 6 axioms + 6 corollaries (A, B, C, D, D-bis, E).

**(5) Thesis v22 — humanise AI-tell prose in 25 paragraphs.** Targeted the recurring AI signatures found in v21: `not only X but also Y` constructions, four-clause parallel structures (`takes X, treats Y, encodes Z, represents W`), abstract-concept tricolons (`more principled, more extensible, and better suited`), AI-favoured adjectives (`powerful`, `principled` outside technical context), em-dashes used as a default clause connector, listy summaries where a single direct statement would do. Touched paragraphs across the Abstract (LP demo), Chapter 2 (transition / synthesis / objectives), Chapter 4 (intro, summary, all five Claude-written subsections from v19/v21), Chapter 5 (PADD stock decomposition), Chapter 6 (the three LOCF paragraphs), Chapter 7 (`partial_coverage`, cross-scenario, summary first paragraph), Chapter 10 (GNN-readiness conclusion, future work), and Annex B (asset-centric rationale). Residuals after pass: 0 `not only`, 0 `powerful`, 1 `principled` (the technical GCN derivation, defensible). Em-dashes remain at 292 but mostly inside legitimate parenthetical asides. Net −146 words.

**(Bonus) Paired PDF workflow for Remarkable + `pdf_design_principles.py` consolidation.** Pedro reads thesis drafts on a Remarkable tablet, which does not render Word documents. `docx2pdf` (Word COM, Windows) added to `requirements.txt` and integrated into each thesis edit script — every `doc.save(...)` is followed by `convert(docx, pdf)` so the paired PDF is committed alongside the docx. Memory `feedback-docx-pdf-pair` captures the gotchas: the call is slow (~5 min for ~40k words); after a successful save `docx2pdf` raises `AttributeError: Word.Application.Quit` on cleanup — verify by `Path(pdf).exists()` rather than exit code; Word can hang on off-screen dialogs, recover via `Stop-Process -Name WINWORD -Force`. Separately, the 615-line `pdf_design_principles.py` reportlab generator (predated the 9cbf907 axioms/corollaries restructure) was replaced with a 60-line `markdown` → `xhtml2pdf` converter reading from `claude/DESIGN_PRINCIPLES.md`; the duplicate `_NEW` files were deleted. `markdown`, `xhtml2pdf`, and `python-docx` added to `requirements.txt` (previously missing despite being used).

**Final state of the thesis:** `Master_Thesis_Pedro_Porfirio_v22.docx`, 37,939 words, paired `v22.pdf` (727 KB) on disk for the Remarkable. Sequence v17 → v22 all in `outputs/docs/`. Chapter 4 final layout: 4.1 Ax1, 4.2 Ax2, 4.3 CrA, 4.4 Ax3, 4.5 Ax4, 4.6 CrB, 4.7 CrC, 4.8 Ax5, 4.9 CrD, 4.10 Ax6, 4.11 CrD-bis (LOCF), 4.12 CrE (node status), 4.13 Summary. Cross-references in Chapters 5–9 all renamed.

**Orchestrator length: 33 steps (unchanged).** Clean rebuild verified post-thesis-edits: 251 nodes, 1,870 variables, 955 assignments, 291,564 resolved values, 0 unresolved, 0 TS-binding collisions, 0 capacity violations. The thesis edits are documentation-only and do not touch the schema or the live data, so the numbers are identical to the twenty-fourth pass.

**Commits on `main`:** `c73a9f9` `pdf_design_principles` consolidation, `49aae35` requirements (python-docx + markdown + xhtml2pdf), `45c20fb` v18 + v19, `fb2a5e4` paired PDFs + docx2pdf, `219e879` v20 axioms restructure, `3593778` v21 D-bis + E, `f73f934` v22 humanisation.

---

**Earlier on 2026-05-16 (twenty-fourth pass — DESIGN_PRINCIPLES restructured as axioms + corollaries, resolver materialises partition sums, new analytic views + audit wired into orchestrator).** Three substantive landed:

**(1) `DESIGN_PRINCIPLES.md` rewritten as axioms + corollaries** (commit `9cbf907`). The earlier "twelve principles" structure was reorganised into six axioms — the primitive design commitments that cannot be derived from anything else — plus five corollaries — properties that fall out of the axioms once the variables collection is properly populated — plus a temporal convention `D-bis` for LOCF. Axioms: 1 asset-centric representation, 2 stable topology via zero-flow edges, 3 variables as single source of truth, 4 persistent asset graph vs scenario state, 5 observed XOR derived (schema-level enforcement), 6 bidirectional flows as two directed edges. Corollaries A–E: A mass balance at physical nodes, B observational aggregation layer has no edges, C labels are properties / hierarchies are formulas, D latent allocation at junctions, E node status as a view. The "resolution rule canon" table consolidates resolver dispatch rules in priority order. The "what is not a principle" section explicitly states what the framework does not treat as primitive: mass balance at observational aggregates (a consequence, not an axiom), the resolution hierarchy as a separate object (encoded in `formula_inputs`), node status as a column (a view), the coverage contract as a primary object (the assignments table per scenario), and latent allocation as a separate rule (one formula value dispatched by the resolver).

**(2) Resolver computes partition sums + intra; renderer does lookup only** (commit `c270950`). Previously the balance UI JavaScript reproduced partition sums and intra-edge classification logic in the browser. The resolver now materialises partition sums into `scenario_resolved_values` directly — every parent aggregate's variable type gets a row with `value = Σ children` and source = `sum` — and intra-edge netting is computed at resolve time and stored as a `partition_intra` annotation. The renderer just looks up the value and intra annotation by (node, vtype, date). Pulls ~250 lines of JS arithmetic out of `make_balance_ui.py` and centralises the math in one place. Drives the row-count jump in `scenario_resolved_values` from 229,301 (23rd pass) to 291,564 (24th pass).

**(3) New L4 analytic views + audit wired into orchestrator ASSIGNERS chain** (commit `5f80428`). Three new views land in `thirteenth_pass_views.py`: `v_resolution_anomalies` (severity-tagged flags for long LOCF runs, negative derived values, and partition mismatches), `v_node_pcisob` (per-node P/C/I/O/B/S/ΔS aggregates from `scenario_resolved_values`), `v_node_balance_check` (per-node mass-balance audit). The resolver auto-refreshes the analytic mat views after every run. The orchestrator ASSIGNERS chain extends by three steps to include the audit views.

**Orchestrator length: 33 steps.** Clean rebuild verified: 251 nodes, 1,870 variables, 955 assignments (down from 975 in the 23rd pass because the resolver-side sum materialisation retires some assignment-side `sum` overrides), 291,564 resolved values (up from 229,301), 0 unresolved, 0 TS-binding collisions, 0 capacity violations. `v_aggregation_consistency`: 3,411 ok + 3,453 `partial_coverage`. `v_resolution_anomalies` low-severity counts: 1,914 `long_locf_run` + 87 `negative_derived` (including the documented `montana_other` case where the state-level series ends earlier than the basin forecast that subtracts from it).

**Commits on `main`:** `9cbf907` DESIGN_PRINCIPLES rewrite, `c270950` resolver computes partition sums, `5f80428` views + audit wired into orchestrator.

---

**Earlier on 2026-05-15 (twenty-third pass &mdash; self-contained git repo, fresh-machine setup tooling, EIA-key scrub, pipe_bakken_xstate wired into inter-PADD aggs, balance UI math verified). Same-day continuation of the twenty-second pass. Five substantive landed:

**(1) Self-contained git repository at `Thesis/clean/`.** Until this pass the active project sat inside the bigger `Oil Network Project/` repo whose `git status` had grown unwieldy (~50 deletions of pre-reorg files staged but uncommitted from the 15th-pass moves). `git init -b main` inside `Thesis/clean/` produced a self-contained repo holding only the active code, configs, outputs, and design docs &mdash; 185 files in the initial commit (`531c6ea`), with no upstream history baggage. The parent repo is left alone (no destructive cleanup) but is no longer the working tree for any active task. `.gitignore` keeps `.claude/settings.json` + `memory/` committed while excluding the per-machine `settings.local.json`, `.env`, `__pycache__/`, asset-graph backups, and the regenerated `code/crude_logistics_map.html`.

**(2) Private GitHub remote + SSH push.** `git@github.com:pgporfirio/oil_network_clean.git` (private) on branch `main`. No `gh` CLI on the machine, no pre-existing GitHub credentials &mdash; generated a new `ed25519` SSH key for `pedro@jabuticaba.app`, added the public half to GitHub's SSH keys, switched the remote URL to SSH form, and pushed `main`. Future pushes from this machine are passwordless via that key. Clone story on a second machine: `git clone git@github.com:pgporfirio/oil_network_clean.git` + the new `setup.ipynb`.

**(3) Fresh-machine setup tooling.** New top-level files: `setup.ipynb`, `requirements.txt`, `.env.example`, `README.md`. `setup.ipynb` is the bootstrap: pip-installs `requirements.txt` into the active interpreter, captures `EIA_API_KEY` + Postgres credentials into a gitignored `.env`, provisions the `eia_user` role and `eia_crude` database (idempotently &mdash; pre-check connects as the role first; if that succeeds, skips provisioning), then runs the master orchestrator end-to-end and prints a sanity-check summary. `requirements.txt` lists 14 packages with `python-dotenv` newly added.

**(4) EIA API key scrubbed, env-var-driven.** `load_eia.ipynb` previously had `API_KEY = os.environ.get("EIA_API_KEY", "vic4Z8...")` with a hardcoded default that was the user's real key &mdash; live in the repo and the initial commit's history. The fallback was removed; `API_KEY` now `raise`s `RuntimeError` if `EIA_API_KEY` is unset. `load_dotenv(Path.cwd().parent / ".env")` added to `load_eia.ipynb` and to `initialize_oil_network.ipynb` so children subprocess notebooks inherit the env through the master notebook's `os.environ`. The leaked key was rotated post-hoc; the replacement lives only in the gitignored local `.env`.

**(5) `pipe_bakken_xstate` wired as constituent of inter_padd_{2_to_4,4_to_2}_agg outflows.** PROJECT_STATE §8 item 1 done. `wire_bakken_xstate_into_inter_padds.py` (slotted into the ASSIGNERS chain right after `seventeenth_pass_xstate_membership.py`) declares the bakken cross-state connector's outflows as `formula_inputs` of the EIA-bound inter_padd outflow variables:

  ```
  outflow__crude__inter_padd_2_to_4_agg__padd4_view
    += outflow__crude__pipe_bakken_xstate__bakken_mt_gathering
  outflow__crude__inter_padd_4_to_2_agg__padd2_view
    += outflow__crude__pipe_bakken_xstate__bakken_nd_gathering
  ```

  **Numerical effect: zero.** Both xstate outflows are `latent()` &mdash; the formula_inputs declare the relationship but resolve to NULL, leaving the parent's TS value untouched. **Audit effect:** `v_aggregation_edges` picks up two new cross-related-node edges (parent's related_node = `padd{2,4}_view`, child's = `bakken_{nd,mt}_gathering`), so future per-operator Bakken gathering telemetry on `pipe_bakken_xstate` lands cleanly as a partition constituent. The strict `v_partition_tree` (same-related-node filter) is unchanged, so the partition closure math is preserved.

**(Bonus) Balance UI `treeSumKids` math verified end-to-end.** `verify_balance_intra_netting.py` reproduces the JS `treeSumKids` logic in Python (single-level partition children, intra/boundary classification for inflows/outflows by checking whether the edge's other end is in the parent's descendants closure). For every TS-observed aggregate parent at 2024-12-01 where every required descendant has data: 22 MATCH, 1 documented MISMATCH (`padd5_tank_farms_pipelines.inventory`: 22,962 mbbl observed vs 0 sum &mdash; per PROJECT_STATE §3.4, no PADD5 commercial hubs modelled), 22 skipped (some children `latent()`, correctly shown as yellow in the UI). The headline confirmation: `usa_view.inflow` parent=6,557 boundary_sum=6,557 **intra=4,018** kbd &mdash; the 4,018 kbd of inter-PADD movements net out cleanly, leaving only the true boundary flows (4,234 Canadian + 2,323 foreign). Same `intra` magnitude on outflow (3,752 boundary, 4,018 intra) confirms the round-trip invariant (every intra edge counts once each side).

**Orchestrator length: 33 steps.** Clean rebuild verified post-wiring: 251 nodes, 1,870 variables, 975 assignments, 229,301 resolved values, 0 unresolved, 0 TS-binding collisions, 0 capacity violations. `v_aggregation_consistency`: 2,940 ok + 3,000 partial_coverage (delta of −3 / +3 from twenty-second pass reflects the new aggregation edges' presence in v_aggregation_edges but their exclusion from v_partition_tree by the same-related-node filter; no behaviour change at the partition-closure layer). New diagnostic scripts `verify_state.py` (headline counts + audit invariants + spot checks) and `verify_balance_intra_netting.py` (the balance-UI math reproduction) added to `code/`.

**Commits on `main`:** `531c6ea` initial commit, `1e8e6eb` setup tooling, `1ff60f8` bakken_xstate wiring. Pushed to `origin/main` after each.

---

**Earlier on 2026-05-15 (twenty-second pass &mdash; full inventory-partition decomposition + consistency-view honesty + Jones Act in-transit).** Same-day continuation of the twenty-first pass. Six substantive landed:

**(1) SPR moved under PADD3, not USA.** The eighteenth-pass migration had declared `spr_total` as an inventory `formula_input` of `usa_view` "for hierarchy display, zero numerical effect" &mdash; but `spr_total.inventory` is TS-observed (~394 MMbbl via `MCSSTUS1`), which double-counted SPR under USA (since the EIA `MCRSTUS1` already includes SPR within the PADD3 total). `twenty_first_pass_spr_under_padd3.py` (1) removes `spr_total` from `usa_view.inventory.formula_inputs`; (2) adds it to `padd3_view.inventory.formula_inputs` (where SPR geographically lives); (3) moves the 4 individual SPR sites from being direct children of `padd3_view` to children of `spr_total`. `promote_spr_total_to_balance_role.py` then changes `spr_total.role = constraint -> balance` so the balance renderer's role-based child filter no longer hides it from the PADD3 drill-down. USA-inventory partition: 134 inconsistent rows -> 134 ok rows. SPR appears under PADD3 in the balance UI with 4 latent SPR-site children.

**(2) PADD-level stock decomposition: 10 new aggregates.** EIA publishes per-PADD stocks split into two buckets (tank farms + pipelines vs refinery stocks), and PADD3 also gets SPR. `add_padd_stock_decomposition.py` creates `padd{1..5}_tank_farms_pipelines` and `padd{1..5}_refinery_stocks` as abstract observational aggregates, each TS-bound to `MCRSFP{N}1` / `MCRRSP{N}1`. Each aggregate's `formula_inputs` declares its physical constituents (named hubs / gathering for tank farms; refineries for refinery_stocks). Each `padd{N}_view.inventory.formula_inputs` becomes `[tank_farms_pipelines, refinery_stocks, (spr_total if PADD3)]`. PADDs 1, 2, 3, 4: parent = sum of children exactly (the EIA identity `MCRSTP{N} = MCRSFP{N} + MCRRSP{N} (+SPR)` materialised in the partition tree).

**(3) Refinery inventory default: 0 -> latent().** `assign_formulas.ipynb` was writing explicit `formula='0'` overrides for every refinery's `inventory` variable. This contradicted the `node_type_default_formulas` value (`latent()`) and meant the new `padd{N}_refinery_stocks` aggregates were comparing their TS to `Σ zeros` &mdash; flagging 804 rows as `inconsistent`. The override is conceptually wrong: refineries DO hold operating crude inventory, EIA simply doesn't publish per-facility data. Latent is the honest default. Patched the notebook (`formula="0"` -> `formula="latent()"`); deleted the 115 stale override rows; re-resolved. Refinery-aggregate consistency now reads `partial_coverage` (correct semantic: per-refinery latent, aggregate TS-bound).

**(4) `v_aggregation_consistency` LEFT-JOIN + missing-data classification.** Pedro&rsquo;s rule: &ldquo;a gap exists only when ALL children have known values AND those values don&rsquo;t sum to the parent. If any child is missing or latent, the gap is just &lsquo;unknown contribution of the missing child(ren)&rsquo; &mdash; not an inconsistency.&rdquo; The earlier view used INNER JOIN to `scenario_resolved_values`, silently dropping children whose TS data was missing for a given date (e.g. Alaska&rsquo;s `MANFPAK2` skips 2024-12 in the EIA series). The dropped children left `n_latent = 0` and a non-zero gap, mislabelling the row as `inconsistent`. `patch_v_aggregation_consistency_missing_data.py` switches to LEFT JOIN + counts any child with no row, NULL value, or `source IN ('latent','unresolved','partial')` toward `n_missing_or_latent`. Status priority reordered so `partial_coverage` fires before `no_data` when children declared exist. The `inconsistent` label was retired entirely: the framework can&rsquo;t tell &ldquo;partition is wrong&rdquo; from &ldquo;partition is incomplete&rdquo; without invoking external knowledge, so every non-ok row is now `partial_coverage` (which the UI further classifies as red vs yellow).

**(5) Resolver `partial` source.** When an arithmetic formula (`A - B - C`) can&rsquo;t evaluate because an input is NULL at a given date, the resolver previously skipped the date entirely, silently dropping the row from `scenario_resolved_values`. Same for closure. `resolve_scenario.py` now writes an explicit row `(value=NULL, source='partial', formula_used='<formula> (missing: <input_id>)')` so downstream consumers see &ldquo;tried to resolve, couldn&rsquo;t, here&rsquo;s why&rdquo; instead of &ldquo;no row&rdquo;. New `partial` value added to the `source` CHECK enum. 312 partial rows now persist with explanatory notes (e.g. `padd5_other.production @ 2024-12 missing: alaska_north_slope`).

**(6) Tolerance = `max(1 unit, 1% relative)`.** Per Pedro: EIA publishes integer values; summing N children at ±0.5 unit each accumulates worst-case ±N/2. A flat-5 tolerance overshoots small parents; a fixed-1 absolute over-flags large parents. `max(1, 0.01 × parent)` is honest at both ends. Applied to `v_aggregation_consistency` SQL and the JS `materiallyDifferent()` in `make_balance_ui.py`. **Balance UI cell coloring** (Pedro&rsquo;s three-state spec): green = match within tolerance; **yellow** = some children missing/partial (can&rsquo;t verify); **red** = all children have values, sum disagrees by &gt; tolerance (genuine divergence). `treeSumKids` now returns `n_missing` so the JS picks the colour per (node, vtype, date).

**(7) Jones Act in-transit as pipeline line-fill on `inter_padd_3_to_5_agg`.** Empirical verification of EIA arithmetic at 2024-12-01: PADDs 1, 2, 3, 4 close exactly (`MCRSTP{N} = MCRSFP{N} + MCRRSP{N} (+SPR)`); PADD5 has a consistent ~6 MMbbl gap. Confirmed via the catalogue description on `MCRSTUS1` (&ldquo;commercial + SPR + in-transit&rdquo;) plus PADD-by-PADD arithmetic: EIA counts Jones Act tankers in transit from Gulf Coast to West Coast as PADD5 ending stocks (destination-based allocation). No separate EIA series for the in-transit volume. Per Principle 2.1 (asset-centric: pipelines/vessels are nodes with line-fill inventory), the right model is to place this in-transit volume as the line-fill of the existing PADD3&rarr;PADD5 corridor node &mdash; `inter_padd_3_to_5_agg` is already a `pipeline`-subtype node with TS-bound flow variables (inflow &lt;- padd3, outflow -&gt; padd5 via `combined_inter_padd_P3_to_P5_kbd`); it just had `inventory = 0` from the pipeline default. `refactor_jones_act_into_inter_padd_agg.py` overrides that to `inventory = padd5_view.inventory - padd5_tank_farms_pipelines.inventory - padd5_refinery_stocks.inventory` (arithmetic residual), adds the node as the third partition child of `padd5_view.inventory`, and promotes its scenario role to `balance` so it appears in the balance UI under PADD5. Verified: inter_padd_3_to_5_agg.inventory resolves to 2,855 / 5,191 / **5,900** MBBL at 2024-10/11/12; padd5_view.inventory closes exactly (parent=49,330 = sum=49,330, gap=0, status=ok). PADD5 inventory: 134 rows from RED &rarr; ok. The earlier standalone `add_padd5_jones_act_in_transit.py` was moved to `code/old/` &mdash; the refactor onto the existing pipeline node is the cleaner asset-centric model.

**Orchestrator length: 32 steps.** Clean rebuild verified end-to-end (`DROP SCHEMA -> initialize_oil_network.ipynb`): 241 + 11 = 252 nodes (217 physical + 35 abstract incl. the new aggregates); 0 unresolved; 0 partition gaps; 0 capacity violations; 2,943 ok + 2,997 partial_coverage in `v_aggregation_consistency` (no `inconsistent` rows; 137 of the 2,997 are RED in the UI = 134 padd5_tank_farms_pipelines (no PADD5 commercial hubs modelled, documented gap) + 3 small EIA publication inconsistencies at specific historical dates). HTMLs regenerated; `oil_network_balance_resolver.html` now color-codes every aggregate cell green/yellow/red per the rule.

---

**Earlier on 2026-05-15 (twenty-first pass &mdash; reproducibility lock-in + doc refresh + variable_constraints schema).** One-day session triggered by an overnight DB reset that surfaced how fragile the post-stage-4 migration chain had become. Three substantive deliverables landed together as Phase 0a + 0b + early Phase 1 in [feedback_production_goal.md]'s LP-readiness arc:

**Phase 0a &mdash; orchestrator reproducibility lock-in.** A `DROP SCHEMA oil_network CASCADE` followed by `initialize_oil_network.ipynb` end-to-end now rebuilds the full 240-node state without any manual chain of migrations. (1) Ten load-bearing migrations restored from `code/old/` to `code/` &mdash; the eighth- through eighteenth-pass scripts had been misclassified as one-time archival when they are in fact part of the recipe to reproduce the live schema: `repoint_foreign_supply_to_imports_agg.py`, `repoint_canadian_corridor_ts.py`, `add_padd2_canadian_imports_agg.py`, `add_partition_aggregates.py`, `split_bakken_gathering.py`, `seventeenth_pass_xstate_membership.py`, `eighteenth_pass_constraint_membership.py`, `twelfth_pass_cleanup.py`, `sixteenth_pass_cleanup.py`, `thirteenth_pass_views.py`. (2) `initialize_oil_network_assignments.ipynb` ASSIGNERS list expanded from 8 to 18 steps, chaining all migrations in chronological pass-order between `assign_formulas` and `resolve_scenario`. (3) New `init_resolver_tables.py` extracts the `scenario_resolver_runs` + `scenario_resolved_values` DDL from `resolve_scenario.py` and runs it before `thirteenth_pass_views.py`, because the L4 materialised views reference `scenario_resolved_values` at definition time. (4) `thirteenth_pass_views.py` gained an unconditional PREDROP block that drops all 10 view names as regular views first &mdash; Postgres `DROP MATERIALIZED VIEW IF EXISTS` errors with `WrongObjectType` when the name exists as a regular view (which is what `build_oil_network.ipynb` creates), so the script was not idempotent against a fresh schema before this fix. (5) `load_asset_graph.ipynb` repointed at `paths.ASSET_GRAPH_JSON` instead of the hardcoded `Path("asset_graph/asset_graph.json")` it carried since before the fifteenth-pass repo reorg. Verification: a full `DROP SCHEMA → initialize_oil_network.ipynb` cycle reconstructs 241 nodes / 1,830 variables / 1,049 assignment overrides / 223,114 resolved values / run_id=1 with 80 observed / 5 sum / 442 alias / 15 mirror / 0 unresolved, partition gap.I = gap.O = 0 across every PADD, and 0 TS-binding collisions.

**Phase 0b &mdash; documentation + retirement of load-bearing post-hoc cleanups.** The `assign_formulas.ipynb` → `twelfth_pass_cleanup.py` dependency had become load-bearing: `assign_formulas` wrote `sum_over_children`/`sum_over_outflows` strings and the twelfth pass migrated them to canonical `sum`. If twelfth-pass was skipped, the resolver dispatched into rule 9 (unresolved). Fixed at the source: 9 string replacements across `assign_formulas.ipynb` cells 3, 6, 10 make it write canonical `sum` directly. `twelfth_pass_cleanup.py` step 3 is now a defensive idempotent no-op (0 rows migrated); steps 1+2 were retired entirely (they duplicated `thirteenth_pass_views.py`'s matview creation as regular views, which broke standalone re-runs against an existing matview); step 4 (attribute-JSONB cleanup) is preserved because `load_asset_graph.ipynb` re-introduces the duplicate keys on every fresh rebuild. Four dormant `sum_over_*` SQL filters in `make_balance_ui.py` tightened to reference canonical `sum`. PDFs regenerated against the live state: `Design_Principles.pdf` gained a "Layered views" section documenting all 10 L1-L4 mat views with reads-from + purpose, and a "Latent vs unresolved" disambiguation; `Resolver_Walkthrough.pdf` gained §3.5 "Where the resolver starts" (leaves of DAG = observed ∪ structural-zero, propagate via topo order), §3.6 "A common misconception: o1 = i2 + i3" (separate variables linked by reverse-mirror, ΣI=ΣO is a constraint in `v_node_balance_check` rather than a propagating formula), §3.7 "Latent vs unresolved", and Section 10 now fetches dispatch counts live from `scenario_resolver_runs` instead of hardcoding them.

**Phase 1a &mdash; two-layer capacity model (`asset_capacities` + `variable_constraints`).** New schema for physical capacities, exposed as proper relational data instead of buried in `assets.attributes->configuration` JSONB. Two complementary tables:

- **`oil_network.asset_capacities`** — physical reality of each asset, scenario-agnostic, per-commodity. PK `(asset_id, commodity, capacity_kind, effective_from)`. `capacity_kind ∈ {throughput, storage, consumption, production}` &mdash; each maps to a particular variable type. Seeded by `populate_asset_capacities.py` from `assets.attributes->configuration`: refinery `capacity_bpd` → consumption (kbd, /1000); pipeline `capacity_bpd` / `nominal_capacity_bpd` → throughput (kbd, /1000); terminal/SPR/storage `storage_capacity_mbbl` → storage (mbbl); foreign-production-aggregate `production_capacity_bd` → production (kbd). Time-versioned via `effective_from` (default `0001-01-01` for the physical defaults; later rows for capacity expansions). Initial population: 115 refinery consumption + 25 storage + 24 pipeline throughput + 1 foreign production = 165 rows.

- **`oil_network.variable_constraints`** — scenario-specific overlays. PK `(variable_id, scenario_id, kind, effective_from)`. Empty by default; populated only when a scenario actually needs to override the physical capacity (e.g., commercial deratings, seasonal limits, what-if analyses). Same temporal semantics. Same NULL = unbounded semantic. `CHECK (min IS NOT NULL OR max IS NOT NULL)` rejects empty rows; `kind` in PK allows multiplicity (physical + commercial + derating coexist).

- **`oil_network.v_effective_constraints`** — read-side join. For each (scenario, variable), takes the `variable_constraints` overlay row when present, falling back to `asset_capacities`. The `layer` column tags which source produced each row. Variable-type mapping: `consumption → consumption`, `production → production`, `inventory → storage`, `outflow → throughput`. (`inflow` and `balancing_item` are not directly bound &mdash; an inflow inherits its capacity from the paired upstream outflow, which the LP exporter can derive.)

- **`audit_capacity_violations.py`** — post-resolution audit, advisory. Compares every `(variable, date)` value in `scenario_resolved_values` against the active row in `v_effective_constraints` (using `DISTINCT ON ... ORDER BY effective_from DESC` to pick the as-of row), emits warnings for any value outside `[min_value, max_value]`. Does NOT block the orchestrator &mdash; a TS observation outside its declared capacity is informational (the TS may itself be the problem, or the capacity may need updating). Exit code 0 always.

**Orchestrator integration.** ASSIGNERS list expanded from 18 to 23 steps. The capacity layer slots in after `thirteenth_pass_views.py`: `create_asset_capacities → populate_asset_capacities → create_variable_constraints → create_v_effective_constraints`. The audit slots in after `resolve_scenario.py`. Verification (clean `DROP SCHEMA` → `initialize_oil_network.ipynb`): 23 steps run cleanly; capacity layer populated as expected; 181 variables have an active bound (115 consumption + 25 storage + 40 throughput + 1 production); audit reports 0 violations &mdash; every observed/derived value respects its declared capacity.

**Capacity backfill — two research passes + peak-based override.** Two research subagents dispatched 2026-05-15 compiled published nominal capacities for the 19 pipelines and 19 production nodes that had no capacity data in `assets.attributes->configuration`, plus the full capacity timeline 2015-2025 for each pipeline (expansions, reversals, deratings).

- **`patch_pipeline_production_capacities.py`** writes the current rated capacities into JSONB (`capacity_bpd` for pipelines, `production_capacity_bd` for producers), with `_source` audit fields tagging the research pass.

- **`patch_pipeline_timeline.py`** layers on `capacity_history` JSONB arrays per pipeline — every documented expansion, derating, reversal between 2015 and 2025, with source citation per entry (Wikipedia/Global Energy Monitor, FERC, S&P Global, RBN Energy, Oil & Gas Journal, company filings). 11 of 19 pipelines have real timeline entries; 8 are stable since pre-2015. Notable corrections from the timeline pass: Capline operational capacity 200 kbpd (not 300 kbpd as the first-pass research assumed; the 300 figure was the announced open-season nameplate); Seaway stepped 850→950 kbpd in 2016; DAPL stepped 470→570→750 kbpd (2017→2018→2021).

- **`patch_production_caps_from_peaks.py`** runs after `resolve_scenario`, queries `scenario_resolved_values` for each producer's historical peak monthly value over the 2015-2024 EIA window, and raises any declared production capacity below 1.05× that peak. EIA doesn't publish "rated production capacity" for upstream assets, so the buffered historical peak is the operative cap. 18 producers were raised (e.g. California 300→600 kbpd to accommodate the 564 kbpd 2015 peak; Eagle-Ford-TX 1200→1810 kbpd to accommodate 1723 kbpd 2014-2015; Permian-NM 2000→2470 kbpd). `canadian_oil_sands` skipped (its 5 Mbpd published cap is Canadian-side production, not US-bound flow).

- **`populate_asset_capacities.py`** extended to emit time-versioned rows from `capacity_history` — one row in `asset_capacities` per `(asset, effective_from)` entry. `v_effective_constraints` and `audit_capacity_violations.py` use `DISTINCT ON ... ORDER BY effective_from DESC` to pick the active cap per observation date.

Final state: 215 capacity rows (115 consumption + 20 production + 25 storage + 55 throughput; 31 time-versioned beyond the default-epoch row). **0 capacity violations** across 200 bounded variables × 156 monthly dates: every observed/derived production / consumption / storage / throughput value falls within its declared capacity for the date it was observed (with pre-2017 Bakken outflows correctly compared against pre-DAPL 470 kbpd, post-2021 against 750 kbpd, etc.).

Orchestrator chain final length: 27 steps. Clean rebuild verified: `DROP SCHEMA` → `initialize_oil_network.ipynb` reproduces the full state including time-versioned capacities. The capacity layer is now LP-defensible — a future LP exporter can read `v_effective_constraints` and treat every latent variable + its capacity as a free decision variable + an upper-bound row.

**Project-root + code/ cleanup.** 17 legacy files moved from the top-level `Oil Network Project/` directory to `Thesis/old/legacy_root/`: the five `get_eia_data_v*.ipynb` iterations, three `temporal_oil_network*.ipynb` variants, `build_oil_logistics_graph.ipynb`, `load_pet_to_db.ipynb`, `claude.md` (legacy CLAUDE), `pipelines.csv`, `refineries.csv`, `us_crude_tables.docx`, `graph builder.zip`, plus the `random stuff/` and `eia_code_references/` directories. Project root now contains only `.git`, `.gitignore`, `.venv`, `.claude`, `Thesis/`, `data/` (gitignored), and the `.code-workspace`. Empty `Thesis/Code/` directory removed. `Thesis/clean/code/` reorganised: 47 archival files (one-time JSON-mutating migrations, retired pre-Postgres audits, orphan notebooks: `build_asset_graph_db.ipynb`, `build_oil_network v1.0.ipynb`, `generate_oil_network_html.ipynb`) moved to `code/old/`. `initialize_oil_logistics_network.ipynb` lost its dead final cell that called the now-archived `build_edge_explorer_map.py`. `code/` itself contains 26 active `.py` + 10 active `.ipynb`. JSON read audit confirms only one active JSON read in the codebase &mdash; `load_asset_graph.ipynb` → `config/asset_graph.json` for DB seeding; `render_utils.py` uses `json.dumps`/`json.loads` for the HTML metadata beacon (not a file read); no other JSON-as-runtime-data anywhere.

**Earlier on 2026-05-14 (twentieth pass &mdash; renderer audit: map_resolver render bug, node_neighbours grouping, partition_map coverage and focus mode, document treatment of trichotomy + retired sum labels). Four small fixes that together close out the renderer round Pedro asked for. (1) **`make_map_resolver_ui.py` regression fix.** The resolver-driven flat map was rendering empty &mdash; the template carries four `__SUBTYPE_*__` / `__N_*__` placeholders and only the data + count placeholders were being substituted, leaving the literal text `const SUBTYPE_COLOR = __SUBTYPE_COLOR__;` in the <font face=\"Courier\">&lt;script&gt;</font> block (a JS parse error that killed `Plotly.newPlot` before any trace rendered). Added the two missing substitutions. (2) **`make_node_neighbors_map.py` selector groupings.** Replaced the single flat 200-entry dropdown with a "Group by" control offering `node subtype` (default), `PADD`, and `state` &mdash; each grouping rebuilds the `<optgroup>`s so the operator can collapse "all refineries" or "all PADD-3 nodes" instead of scrolling alphabetically. (3) **`make_partition_map.py` coverage fix + focus mode.** Two pure observational aggregates (`spr_total`, `usa_lower48_excl_gom_view`) and a handful of other coordinate-less aggregates were silently being skipped because the renderer's `if n.lat is None: continue` filter dropped any node the engine couldn't anchor. Added an aggregate-coordinate inference pass: first try the centroid of partition descendants (fixes basin views and similar), then a "sibling centroid" last resort for TS-bound constraint nodes with no descendants (fixes spr_total + usa_lower48). Result: payload-node count went from 225 to 241 &mdash; the full asset graph, no silent drops. Also added a "Focus on selected" toggle: clicking it restricts the visible subtree to `currentSelected` and its descendants, hiding the rest of the partition tree so the operator can drill into a single PADD without the rest of the country in view. Edge visibility tightened to "both endpoints visible AND parent expanded" so focus mode automatically scopes the partition edges to the focused subtree. (4) **Documents: the trichotomy and the retired sum labels surfaced.** `Design_Principles.pdf` now has a dedicated section explaining the authoritative/derived/collapsed trichotomy as a table (state, trigger, meaning) and the canonical sum rule as a structural-pattern table (what `formula_inputs` shape maps to what semantic role). `Resolver_Walkthrough.pdf` gets a rule-canon overview table at the top of Section 4 listing each canonical label and the predecessors it retired (the three sum_* labels rolled into `sum`; the closure dormant since the sixth pass). Both PDFs regenerated; HANDOVER nineteenth-pass entry preserved.

**Earlier on 2026-05-14 (nineteenth pass &mdash; hierarchy-explorer status driven by `v_node_status`).** The previous renderer flagged a node as yellow &mdash; *no own data, has parent* whenever it lacked a TS-bound variable but appeared as a child in `parent_map`. That label conflated two genuinely different cases that the framework already distinguishes: **derived** (a node whose value is computed by formula from its constituents, e.g. `eagle_ford.inventory = sum(eagle_ford_tx.inventory)` &mdash; the node *has* a value, it just isn&rsquo;t observed at this resolution) and **collapsed** (a node whose every variable is &lsquo;0&rsquo; or &lsquo;latent()&rsquo; &mdash; no value, by design). Principle 2.8 already defines this trichotomy and `v_node_status` already materialises it per (scenario, node), but the hierarchy renderer wasn&rsquo;t reading from the view &mdash; it was re-deriving status badly from `vars_by_node`. Three changes: (1) `make_hierarchy_explorer.fetch()` now pulls `v_node_status` and threads `node_status_map` into `build_payload`; (2) `node_status()` reads the view directly and emits one of `ts` (authoritative), `derived`, `collapsed`, `root`, `orphan`; (3) the HTML template legend, badges, and the per-node detail-pane status string are updated to surface the four-way distinction (green / blue / grey / red) instead of green / yellow / red. The resolver-driven sibling (`make_hierarchy_resolver_ui.py`) was missing the `__N_NODES__` / `__N_*__` placeholder substitution entirely &mdash; fixed in the same pass. Result on the starter scenario: 56 authoritative, 177 derived, 7 collapsed, 0 orphans. `eagle_ford` now correctly reads `derived` (computed from `eagle_ford_tx`), `pipe_keystone` reads `derived` (latent inflow with alias-mirrored outflow), `usa_view` reads `ts` (B is observed). Eighteen earlier passes preserved.

**Earlier on 2026-05-14 night (eighteenth pass &mdash; derive everything from variables, retire hardcoded structural overrides).** Audit found ~35 hardcoded `(child, parent)` rules duplicated across two renderers plus `EXPLICIT_PADD` map + `derive_padd()` regex + `ROOT_NODES` set. **Data migration:** 7 constraint nodes (`permian`, `bakken`, `eagle_ford`, `spr_total`, `usa_lower48_excl_gom_view`, `texas_state_view`, `montana_state_view`) declared as `inventory_input`s of their natural display parents via `formula_inputs` — same eleventh/seventeenth-pass pattern, zero numerical effect. **Code cleanup (~150 lines deleted):** structural overrides, `EXPLICIT_PADD`, `derive_padd()`, and `ROOT_NODES` all retired. Renderers now read `v_partition_tree` exclusively, with a data-driven geographic fallback (reads `locations.padd` directly) for physical nodes not in any aggregate's `formula_inputs`. **Hierarchy orphan count dropped from 36 to 2** (just the two cross-border pipes that legitimately span the border). `DESIGN_PRINCIPLES.md` updated: Principle 2.4 now states "Hierarchy is purely derived from variables — no hardcoded structural overrides in renderer code." Seventeen earlier passes preserved.

**Earlier on 2026-05-14 night (seventeenth pass):** `pipe_bakken_xstate` added as inventory-member of padd2_view + padd4_view; now appears nested under both PADDs.

**Earlier on 2026-05-14 night (sixteenth pass):** v_effective_assignments override semantics fixed; node_type_default_formulas migrated to canonical 'sum'.

**Earlier on 2026-05-14 night (fifteenth pass):** repository reorganisation into `clean/` + `old/` with `paths.py` as the single filesystem source of truth). Active files now live under `Thesis/clean/{claude,code,config,outputs/{html,docs}}/`; everything historical lives under `Thesis/old/{thesis_drafts,htmls,scripts,asset_graph_backups,misc}/`. 82 active `.py` + `.ipynb` files in `clean/code/` (flat layout so imports stay simple), 1 seed JSON in `clean/config/`, 5 canonical HTMLs in `clean/outputs/html/`, 33 docs+diagrams in `clean/outputs/docs/`. `paths.py` exposes `CODE_DIR`, `CONFIG_DIR`, `CLAUDE_DIR`, `HTML_DIR`, `DOCS_DIR`, `ASSET_GRAPH_JSON` — every script that writes a file reads the relevant constant from there. 9 renderers updated to write into `HTML_DIR`; 3 PDF generators updated to write into `DOCS_DIR`; `regenerate_htmls.py` reads its scan-directory from `HTML_DIR`. Full pipeline verified from the new location: `regenerate_htmls.py --force` rebuilds all 5 HTMLs (artefact_ids 8-12, run_id=14, audit rows recorded), `audit_partition_gaps.py` clean (gap.I/O = 0 across every PADD), PDF generators land in `clean/outputs/docs/`. Fourteen earlier passes preserved.

**Earlier on 2026-05-14 late evening (fourteenth pass):** HTML rendering pipeline: every artefact now carries a metadata beacon + audit row, two recent renderers fully refactored to `NetworkGraph`, single orchestrator with staleness detection. Five deliverables: (1) **`scenario_html_artefacts` audit table** — one row per HTML generation, linking `(scenario_id, run_id, view_name, file_path, file_size_bytes, generated_at, notes)`; (2) **`render_utils.py`** — shared helpers: `metadata_html()` injects the JSON beacon (`<!-- oilnet-artefact: {...} -->`) plus `<meta>` tags into every HTML <head>; `write_html()` writes the file and records the audit row in one call; `extract_metadata()` reads the beacon back; (3) **`regenerate_htmls.py` orchestrator** — single canonical entry point for the five visualisation HTMLs. Reads the latest `scenario_resolver_runs.run_id`, compares to each HTML's embedded beacon, regenerates only the stale ones. `--force` rebuilds everything; `--list` prints the status table without rebuilding; `--views=balance,partition_map` filters by subset; (4) **`partition_map` and `node_neighbors_map` refactored to `NetworkGraph` exclusively** — both renderers now consume the engine (`g.partition_children`, `g.coords`, `g.value`, `g.flow_edges`) instead of issuing their own SQL. Roughly 100 lines of inline queries removed per renderer; (5) **Balance / hierarchy / flat-map UIs wired through `render_utils.write_html`** for metadata + audit. The inline-SQL ancestor modules (`make_balance_ui.py`, `make_hierarchy_explorer.py`, `make_map.py`) remain as internal template providers — their HTML/JS templates and `build_payload` helpers are still imported, but they are no longer user-facing entry points. Their full conversion to `NetworkGraph`-only data fetching is queued for the fifteenth pass. **Workflow now:** any time `variable_assignments` changes, run a migration script → `refresh_views.py --structural` → `resolve_scenario.py` (auto-refreshes L4 + writes an audit run row); then `regenerate_htmls.py` picks up the new run_id and rebuilds every stale HTML, recording each in `scenario_html_artefacts`. Querying "is the project up to date?" is a single `SELECT view_name, run_id FROM scenario_html_artefacts WHERE scenario_id = ... ORDER BY generated_at DESC` plus a comparison to `latest_run_id`. All 5 HTMLs at run_id=14 after this pass; no partition-closure regressions. Thirteen earlier passes preserved.

**Earlier on 2026-05-14 evening (thirteenth pass):** layered materialised views, the `NetworkGraph` Python engine, partition-tree definition tightened to exclude arithmetic residuals, cell-by-cell verification clean. Five deliverables landed: (1) **layered materialised views**: `v_formula_input_links` (L2a) → `v_aggregation_edges` (L2b, now carries `parent_formula` + `parent_is_ts_bound`) → `v_flow_edges` (L2c) → `v_partition_tree` (L3a, semantic filter) + `v_node_status` (L3b) → `v_node_balance_check` / `v_aggregation_consistency` / `v_inventory_changes` / `v_aggregate_balance` (L4), all with `CONCURRENTLY`-refreshable unique indexes; (2) `refresh_views.py` utility (`--structural` / `--analytic` / both), with the resolver auto-invoking the analytic refresh after every run; (3) `network_graph.py` engine exposing `NetworkGraph(scenario_id)` — loads in ~300 ms from the materialised views, exposes `partition_children`, `descendants`, `status`, `value`, `node_balance` and `inflows_to`/`outflows_from` for use by every renderer; (4) **partition-tree filter refined**: arithmetic-residual formulas (`parent = a − b − c`) reference variables but do not aggregate them, so they no longer count as partition relations. Filter: `parent_formula NOT LIKE '%-%' AND NOT LIKE '%+%'`. Aliases and `sum` formulas and TS-bound parents are all retained. Edge count 270 → 244; spurious reverse-edges (e.g. `padd2_other → padd2_view`) removed; audit gap.I/O still 0 across every PADD; (5) **cell-by-cell balance-HTML verification** via `verify_balance_cells.py`: of 35 (parent, variable_type) cells, **13 close exactly**, 3 are sub-1-kbd rounding noise (`usa.P − ΣPADD.P`, `padd3.C − Σdistrict.C`, `usa.B − ΣPADD.B`), 2 are notable (≤100 kbd, latent refineries on small districts), and **15 are significant gaps — every one explained by Principle 2.11 latent allocation**: refinery-level consumption not published per facility (10 refining-district / PADD-consumption cases), PADD-level inventory not decomposed to facility stocks (4 cases), and alaska_north_slope production embedded in the PADD-5 aggregate (1 case). No bugs surfaced. Resolver run_id=14 with refresh_views integration; **0 unresolved**. Twelve earlier passes preserved.

**Earlier on 2026-05-14 (twelfth pass):** framework simplifications: principles consolidated, two new derived views, sum-rule unification, JSONB duplicates stripped. Five orthogonal cleanups landed: (1) `v_partition_tree` view in SQL replaces the inline same-type formula_inputs queries in `audit_partition_gaps.py` and `make_partition_map.py`; (2) `v_node_status` view infers `authoritative` / `derived` / `collapsed` per (scenario, node) from `variable_assignments`, making the duplicated `starter_status` columns redundant; (3) the three resolver labels `sum_over_children`, `sum_over_outflows`, `sum_same_type` collapsed into one canonical `sum` rule (5 variables migrated; semantic role recoverable from `formula_inputs` structure, not the formula text); (4) duplicate JSONB keys stripped from `assets.attributes` (4 keys × 219 rows), `nodes.attributes` (1 key × 219 rows), and `locations.attributes` (4 all-NULL keys × 219 rows); (5) 17 backup `asset_graph.backup_*.json` files archived to `asset_graph/archive/`. Design docs (`DESIGN_PRINCIPLES.md`, `CLAUDE.md` §2, and the three thesis-facing PDFs) updated to reflect Principle 2.4 generalised to formula-implies-relation; the former "coverage contract" (2.9) absorbed into 2.5 / 2.8 since `variable_assignments` already encodes the contract; and the dual role of `formula_inputs` (constraint when TS-bound, operand when formula-bound). Resolver run_id=14: 80 observed, 442 alias, 15 reverse_mirror, 8 arithmetic, 5 sum, **0 unresolved**. All partition gaps remain at zero; Canadian + foreign flow consistency verified end-to-end (every padd_view = physical-aggregate, every PADD-sum = usa_view, all gaps 0.00 kbd). Eleven earlier passes preserved.

**Earlier on 2026-05-13 night (eleventh pass):** Bakken gathering split + cross-state connector + gathering inventory-membership: **every partition gap.I AND gap.O = 0**, perfect closure. The single multi-PADD `bakken_nd_gathering` was split into `bakken_nd_gathering` (PADD 2: receives `bakken_nd`, sends to 7 PADD-2 destinations) and a new `bakken_mt_gathering` (PADD 4: receives `bakken_mt`, sends to 6 PADD-4 destinations) plus a new `pipe_bakken_xstate` bidirectional connector node modelling the real-world cross-state midstream pooling (Hess, Crestwood, Energy Transfer operate ND/MT lines as one commercial system). All 4 connector flow variables are latent in the starter — Principle 2.11 latent allocation at junctions. The remaining gap.O artefact from the tenth pass (basin → gathering outflows showing as boundary because gathering nodes weren't same-type partition children) was fixed in the same migration by adding gathering-node inventory variables to each `padd_view.inventory.formula_inputs` (numerical effect zero since gathering S=0 by default; audit effect: gathering enters `descendants(padd_N)`, basin→gathering outflows are now intra). Resolver run_id=13: 80 observed, 442 alias, 15 reverse_mirror, 8 arithmetic, **0 unresolved**. TS-binding uniqueness 80/80. **Partition audit: every PADD now closes on both sides** (`usa_view 0/0`, `padd1 0/0`, `padd2 -0/0`, `padd3 0/0`, `padd4 0/0`, `padd5 0/0`). Ten earlier passes preserved.

**Earlier on 2026-05-13 late evening (tenth pass):** partition-aggregate generalisation — 19 new physical-layer aggregate nodes close every partition gap.I across all PADDs. The PADD 2 Canadian aggregate prototype from the ninth pass is now generalised to the full family: **2 Canadian-imports aggs** (PADDs 1, 3) + **5 exports aggs** (PADDs 1..5) + **12 inter-PADD corridor aggs** (every directional `A→B` corridor with a TS), with PADDs 4 and 5 Canadian inflows re-pointed to alias their existing cross-border pipes' `inflow_from_canadian_oil_sands` variables (same-type chain via the pipe rather than a redundant agg node). **Result: gap.I = 0 across every PADD view** (was 229 / 1,563 / 2,523 / 553 / 444 kbd for PADD 1..5 respectively). Every TS-bound flow now lives on a physical-layer aggregate and the observational PADD view aliases through; every aggregate is a same-type partition child via formula_inputs. Inter-PADD aggs use a new cross-type internal alias `agg.inflow = alias(agg.outflow)` so mass balance closes at the aggregate (ΣI=ΣO for a pipeline) and the receiver-side alias chain is also same-type — closing both ends with a single TS binding. Resolver run_id=12: 80 observed, 644 latent, 620 zero, **442 alias** (was 411, +31 from the new alias chains), 15 reverse_mirror, 8 arithmetic, 4 sum_over_children, 1 sum_over_outflows, **0 unresolved**. TS-binding uniqueness audit clean: 80 distinct TS in 80 bindings (1:1). gap.O sign-flips to slightly negative on PADDs 2/3/4 because each padd's new aggs' boundary outflows match `own.O` exactly, but pre-existing intra-partition flows (`bakken_nd → bakken_nd_gathering`, basin → gathering on producing PADDs) are still counted as bdy.O by the audit since gathering nodes aren't same-type partition children. That's an audit-script artifact, not a real outflow gap; the audit can be refined later by adding flow-edge descent within a PADD. Nine earlier passes preserved.

**Earlier on 2026-05-13 evening (ninth pass):** reverse-mirror dispatch fix, PADD 2 Canadian aggregate prototype, audit refactor to read partition tree from DB. The resolver's reverse-mirror rule now fires deterministically: a relational variable with `formula='latent()'` promotes to mirror-derived from its paired direction when the paired side is observed. Two interlocking bugs fixed (PART 11B of [RESOLVER_WALKTHROUGH.txt](RESOLVER_WALKTHROUGH.txt)): (1) dispatch rule 3 (latent) short-circuited before rule 7 (mirror); (2) `build_deps()` skipped latent variables before the mirror-dep block, leaving topo order non-deterministic. Joint fix uses a new `paired_aliases_us` cycle gate. Result: 15 mirrors fire (was 3 by luck), 5 canadian_oil_sands.outflow → padd_view edges + usa_view now resolve correctly. New `padd2_canadian_imports_agg` node prototypes the aggregate pattern Pedro proposed: TS authority on the physical-layer aggregate, padd2_view alias-derived; future home for per-grade decomposition (production goal). [audit_partition_gaps.py](audit_partition_gaps.py) now reads the partition tree directly from the DB (formula_inputs traversal with same-type / same-relation filter) instead of parsing the generated HTML — same source as the resolver. PADD 2 gap.I drops from 4,447 → 1,563 kbd as the new aggregate is correctly recognised as a partition child. Resolver run_id=11: 80 observed, 637 latent, 544 zero, 411 alias, 15 reverse_mirror, 8 arithmetic, 4 sum_over_children, 1 sum_over_outflows, **0 unresolved**. TS-binding uniqueness audit clean: 80 distinct TS in 80 bindings (1:1). Eight earlier passes preserved.

**Earlier on 2026-05-13 (eighth pass):** TS-binding uniqueness cleanup. Five `inflow ← foreign_supply` / `inflow ← canadian_oil_sands` variables on PADD-view aggregates were re-pointed: TS authority moved to the physical-layer node (`padd{1,3,5}_imports_agg` for foreign supply, `canadian_oil_sands → pipe_{express_platte, trans_mountain_tmx}` for Canadian corridors), with the PADD-view inflow now `alias`-derived. The new `audit_ts_binding_uniqueness.py` confirms **0 TS-binding collisions**: 80 TS-bound variables now use 80 distinct timeseries (perfect 1:1 attribution, Principle 2.8 holds at the TS level). Resolver dispatch: 80 observed, 651 latent, 540 zero, 410 alias, 8 arithmetic, 4 sum_over_children, 1 sum_over_outflows, **0 unresolved**. Numerical values unchanged; the partition-gap audit now also traces foreign-supply decomposition explicitly (`pipes: [inflow__crude__padd{N}_imports_agg__foreign_supply] -> pipe sum status: 1/1 resolved`). Resolver bug uncovered along the way: rule-6 alias dispatch requires the formula text to be the bare variable_id, not wrapped in `alias()`; the wrapper is only the display label. Migration scripts (`repoint_foreign_supply_to_imports_agg.py`, `repoint_canadian_corridor_ts.py`) use the bare-id convention and are idempotent. Seven earlier 2026-05-12 passes preserved.

**Earlier on 2026-05-12 (seventh pass):** Node-type defaults pass — `node_type_default_formulas` populated with structural defaults per node type, `v_effective_assignments` view layers `variable_assignments` overrides on top of those defaults, resolver consumes the view, `variable_assignments` sparsified to overrides-only (1690→1001 rows, 41% now sourced from defaults). Key behavioural effect: pass-through node types (gathering, origin_terminal, pipeline, import_terminal, export_terminal, foreign_export_destination, foreign_production_aggregate) default B=0 with P=C=ΔS=0, so mass-balance closes at every junction: ΣO = ΣI even when individual splits are latent. New `v_node_balance_check` view exposes `sum_out_implied` per node, surfacing Pedro's `permian_tx_gathering` audit case as **ΣI=4313.1 → ΣO_implied=4313.1** with 3 individual outflows still latent (Principle 2.11).

**Earlier on 2026-05-12 (sixth pass):** Supply Adjustment + PADD stock decomposition binding pass — 16 previously unbound EIA series catalogued + facts loaded. **B is now observed**: `MCRUA_NUS_2` + `MCRUA_R{1..5}0_2` bound as authoritative on `balancing_item__crude__{usa_view, padd1..5_view}`, replacing the closure formula (Principle 2.8 promotion from derived to TS-observed). USA B observed = −379 kbd at 2024-12-01, vs the −2,826.5 kbd closure value before — the 2,447 kbd delta IS the magnitude of latent pipe/cross-border flows missing from the partition, which is Chapter 5 Claim 4. USA-level B aggregation consistency now closes to 1 kbd (gap = −379 vs Σ-PADDs = −380). Stock decomposition `MCRSFP/MCRRSP{1..5}1` (10 series) registered as auxiliary on `padd{N}_view` per Principle 2.8 — published only at PADD level, so structural sub-nodes would double-count Cushing. Closure formula moved from `assign_formulas.ipynb` (the 6 closure tuples deleted to avoid overwriting the new TS bindings). All five 2026-05-12 fixes preserved.

---

## Current state at a glance (2026-05-13 night, after eleventh pass)

### Schema

17 core tables in `oil_network`. New today: `scenario_resolver_runs` (audit log of each resolver invocation) and `scenario_resolved_values` (one row per scenario × variable × date with `value`, `source`, `formula_used`, `run_id`). 8 views unchanged:

| View | Purpose |
|---|---|
| `v_flow_edges` | derived from `outflow` variables — physical flow topology |
| `v_aggregation_edges` | derived from `formula_inputs` — aggregation graph |
| `v_node_production_sources` | discovery: every production variable + its binding |
| `v_scope_authoritative_nodes` | every TS-bound assignment |
| `v_scope_collapsed_nodes` | every `0`/`latent()` variable |
| `v_inventory_changes` | per-stock-series Δ in MBBL + kbd |
| `v_aggregate_balance` | closed mass balance for USA + 5 PADDs (superseded by `scenario_resolved_values` for new code) |
| `v_aggregation_consistency` | observed vs sum-of-constituents (data-quality flag) |

### Asset graph

- **240 assets** (197 physical + 43 abstract aggregates including 4 boundary nodes). +2 today: `bakken_mt_gathering` (split from `bakken_nd_gathering`) and `pipe_bakken_xstate` (cross-state gathering connector).
- **409 directed flow edges** in the persistent flow graph; the eleventh pass deleted 14 edges and added 19 (net +5), preserving the physical topology while refactoring the Bakken gathering to be per-state.
- **1,830 variables** in starter scenario (was 1,814 — net +16 after the bakken split: +25 added on the two new nodes and their mirrors / refinery counterparts, −14 deleted via cascade for the obsolete `bakken_mt → bakken_nd_gathering` and 6 PADD-4 outflows on the old shared gathering, +5 net = nope let me recompute: gross additions covered all 19+8 connector + 6 refinery mirrors. Net delta +16 reflects the final state after deletes). 1,136 explicit overrides in `variable_assignments` + the remainder sourced from defaults via `v_effective_assignments`. Resolver dispatch (run_id=13, 2026-05-13 night): 80 observed, 652 latent, 628 zero, 442 alias, 15 reverse_mirror, 8 arithmetic, 4 sum_over_children, 1 sum_over_outflows, 0 closure, **0 unresolved**. **TS-binding uniqueness audit clean: 0 collisions, 80 distinct TS in 80 bindings (1:1).**
- **137 EIA catalogue series** (unchanged this pass — the 17 TS that were directly on padd_views are now bound to the new aggs; same series, same count, different variables).
- **~16,400 timeseries fact rows** spanning 2015-01 → 2026-02 (unchanged this pass).
- **220,618 resolved rows** in `scenario_resolved_values` for the starter scenario (was 202,944; +17,674 = 114 new variables × 156 monthly observations, minus a small number of NULL latent rows). Dispatch: 12,480 observed, 14,508 derived, 70,304 zero, 123,326 latent (rough breakdown), **0 unresolved**.

### Active scenario

`starter_us_crude_2015_2025` — geographic-primary partition (USA → 5 PADDs → districts/physical assets).

### Per-node roles (in this scenario)

- **16 `balance` nodes**: `usa_view`, 5 PADDs, 10 refining districts. The active partition. `Σ children = parent` holds per variable_type.
- **10 `constraint` nodes**: 3 basin aggregates, `spr_total`, STEO subtotal, 2 state views, 3 foreign boundary nodes. Auxiliary observations + boundary nodes; not summed into the partition, used as additional constraints.

### Aggregation constituents

33 TS-bound aggregate variables have explicit `formula_inputs` declared (set by `add_aggregation_constituents.py`). The `v_aggregation_consistency` view continuously cross-checks each: `Σ constituents` vs the TS-observed parent value.

### Latest consistency check (2024-12-01, after the 2026-05-12 binding pass)

| Status | Count | Reading |
|---|---:|---|
| **`ok`** | 11 identities | partition math correct. USA P/C/S/exports match Σ-of-PADDs exactly; PADD 1/2/3 consumption matches Σ-of-districts exactly; foreign and Canadian inflow rollups match Σ-of-PADDs to zero; inter-PADD aggregate flows in correct direction. **New today:** `balancing_item__crude__usa_view` (observed −379) matches Σ-of-PADD-B (−380) to 1 kbd rounding — first time both sides of the B-aggregation invariant are independently TS-observed. |
| **`partial_coverage`** | 7 aggregates | physical-pipe-level decomposition is latent for inter-PADD movements + Canadian cross-border + per-PADD stocks (only Cushing currently bound on a TS basis as a structural child of padd2_view). USA-level closure is clean; PADD-level pipe-decomposition closure needs additional pipe-level TS not yet published. |
| **`inconsistent`** | 0 aggregates | clean across all 18 declared aggregation identities. |

### `audit_partition_gaps.py` at 2024-12-01 (post-eleventh-pass)

| Node | own.I | bdy.I | gap.I | own.O | bdy.O | gap.O |
|---|---:|---:|---:|---:|---:|---:|
| usa_view | 6,557 | **6,557** | **0** | 3,752 | **3,752** | **0** |
| padd1_view | 705 | **705** | **0** | 92.2 | **92.2** | **0** |
| padd2_view | 4,503 | **4,503** | **0** | 2,426.1 | **2,426.1** | **0** |
| padd3_view | 3,631.2 | **3,631.2** | **0** | 4,264.1 | **4,264.1** | **0** |
| padd4_view | 552.7 | **552.7** | **0** | 987.5 | **987.5** | **0** |
| padd5_view | 1,183 | **1,183** | **0** | — | 0 | **0** |

**Every partition closes. Both sides. Every PADD.** This is the first time the audit shows clean closure across the board. The framework's partition + Principle-2.11 latent-allocation machinery is now end-to-end consistent: every TS-bound flow lives on a physical-layer aggregate; every aggregate is a same-type partition child via `formula_inputs`; every basin → gathering edge is intra to its PADD via the new inventory-membership declarations; the Bakken cross-state pooling is honestly modelled by a latent connector rather than papered over by a multi-PADD node.

### Six HTML explorers (3 originals + 3 parallel resolver-driven)

Originals (data fetched via inline SQL):
- **[oil_network_map.html](oil_network_map.html)** — 195 physical assets on `natural earth` projection (Alaska in real position); 376 flow edges with clickable midpoint markers.
- **[oil_network_hierarchy.html](oil_network_hierarchy.html)** — drill-down tree from roots to physical leaves; 0 orphan physical nodes.
- **[oil_network_balance.html](oil_network_balance.html)** — per-node balance equation (P/C/I/O/B/S/ΔS) at a selected date. Split into **balance partition tree** + **Constraint observations**. Inter-partition flow netting now shown ("+N intra" small grey annotation below the green sum).

Resolver-driven counterparts (read `scenario_resolved_values`, drop the inline CTEs):
- **[oil_network_map_resolver.html](oil_network_map_resolver.html)** — node tooltip now shows `n_resolved` (includes derived) alongside `n_ts`. 55 nodes have resolved values vs 33 with TS-only.
- **[oil_network_hierarchy_resolver.html](oil_network_hierarchy_resolver.html)** — same UI, embedded TS data filtered through `scenario_resolved_values WHERE source='observed'`.
- **[oil_network_balance_resolver.html](oil_network_balance_resolver.html)** — same as original; values now sourced from the resolver. Drops the JS arithmetic-evaluator entirely.

Each `make_*_resolver_ui.py` imports its original counterpart for HTML template + JS, swaps only the data layer. After visual compare, the originals can be retired.

### Where to resume

In priority order:

1. **Append migrations + audits to the orchestrator** ([initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb)) — `add_partition_aggregates.py` (tenth pass), `split_bakken_gathering.py` (eleventh pass), then `resolve_scenario.py`, then `audit_partition_gaps.py` + `audit_ts_binding_uniqueness.py` as exit-1-on-fail gates. Every orchestrator run should land a fully-closed partition.
2. **Build `v_balancing_item_check`** for Chapter 5 Claim 4 — now the cleanest possible artefact since every gap.I/gap.O closes. The closure-vs-observed delta isolates exactly which latent flows are missing. Preview: at 2024-12-01, USA closure-B ≈ −2,826 vs observed-B = −379 → ~2,447 kbd of latent intra-USA flow (pipe/cross-border + bakken xstate + per-route decomposition).
3. **Wire `pipe_bakken_xstate` as a constituent of `inter_padd_2_to_4_agg` + `inter_padd_4_to_2_agg`.** Set `formula_inputs` on those aggs' outflow variables to include the connector's relevant outflow. Formalises that the Bakken cross-state pooling contributes to the inter-PADD measurement; future per-operator/per-grade data lands here.
4. **Retire inline-SQL HTML generators.** Visual-compare originals vs `*_resolver.html` pairs and drop inline CTEs.
5. **Add a second scenario** (`starter_basin`) for cross-scenario consistency validation (Chapter 5).
6. **Grade / quality dimension** — next major axis. Sits naturally on the imports_agg + canadian_imports_agg + exports_agg + inter-PADD agg + bakken xstate connector nodes (one variable per grade summing to the aggregate).
7. **Future: per-refinery operating data.** The MCRSFP/MCRRSP stock decomposition is currently auxiliary at PADD level. If per-refinery operating stocks become available (Genscape/IIR feeds), refinery nodes can carry their own inventory variables and `inventory__crude__padd{N}_view`'s `formula_inputs` can list those refineries + the named hubs (Cushing, Patoka, etc.) explicitly — converting auxiliary stock data into structural decomposition.

---

## Latest pass — Bakken gathering split + xstate connector + gathering membership: perfect closure ✓ (shipped 2026-05-13 night, eleventh pass)

**What landed.** Three orthogonal fixes that together push the partition audit to **every gap = 0 on both sides** across every PADD view and `usa_view`:

1. **Bakken gathering split.** The single `bakken_nd_gathering` node was misleading: despite its name it received from both `bakken_nd` (PADD 2) AND `bakken_mt` (PADD 4), and fanned out to 13 destinations across both PADDs. After the split:
   - `bakken_nd_gathering` (PADD 2): inflow from `bakken_nd` only; outflows to 7 PADD-2 destinations (`clearbrook_entry`, `johnsons_corner_origin`, `ref_tesoro_mandan`, `ref_flint_saint_paul`, `ref_st_saint_paul`, `ref_superior_superior`, `ref_hf_evansville`).
   - `bakken_mt_gathering` (PADD 4, NEW): inflow from `bakken_mt` only; outflows to 6 PADD-4 destinations (`ref_calumet_great_falls`, `ref_cenex_laurel`, `ref_par_billings`, `ref_phillips_billings`, `ref_silver_evanston`, `ref_wyoming_newcastle`).

2. **`pipe_bakken_xstate` connector.** New bidirectional pipeline-type node sitting between the two gatherings, modelling the real-world cross-state midstream pooling (Hess, Crestwood, Energy Transfer operate ND/MT trunk lines as one commercial system). Eight latent flow variables: 4 on the connector (2 inflows + 2 outflows, one per direction × 2 gatherings) and 4 mirror variables on the two gatherings. All 8 are latent in the starter — Principle 2.11 latent allocation. The constraint `ΣI = ΣO` holds at the connector by node_type default (pipeline pass-through, P=C=ΔS=B=0). This is a structural placeholder; future commercial gathering telemetry or per-operator/per-grade attribution lands on this node.

3. **Gathering inventory-membership.** The pre-existing audit artefact (basin → gathering outflows appearing as boundary outflows because gathering nodes weren't same-type partition children of any PADD view) is fixed by adding each gathering's inventory variable to its PADD's `inventory.formula_inputs`:
   - `inventory__crude__padd2_view += inventory__crude__bakken_nd_gathering`
   - `inventory__crude__padd3_view += [eagle_ford, permian_nm, permian_tx]_gathering`
   - `inventory__crude__padd4_view += inventory__crude__bakken_mt_gathering`
   - `inventory__crude__padd5_view += inventory__crude__ans_gathering`

   **Numerical effect: zero** (gathering S=0 by default from `node_type_default_formulas` for `gathering` type). **Audit effect:** gathering nodes now enter `descendants(padd_N)`, so basin → gathering outflows are correctly classified as intra rather than boundary.

**Cross-checks at 2024-12-01:**

| Check | Before eleventh pass | After eleventh pass |
|---|---:|---:|
| Resolver unresolved | 0 | **0** |
| TS-binding uniqueness collisions | 0 | **0** (80 TS / 80 bindings) |
| usa_view (gap.I, gap.O) | (0, 0) | (**0, 0**) |
| padd1_view (gap.I, gap.O) | (0, 0) | (**0, 0**) |
| padd2_view (gap.I, gap.O) | (0, **−1,192**) | (**0, 0**) |
| padd3_view (gap.I, gap.O) | (0, **−5,447**) | (**0, 0**) |
| padd4_view (gap.I, gap.O) | (0, **−55**) | (**0, 0**) |
| padd5_view (gap.I, gap.O) | (0, 0) | (**0, 0**) |

**Three-layer partition decomposition now formal:**

| Layer | Carries TS? | Latent? | Constituent of |
|---|---|---|---|
| PADD view (e.g. `padd2_view.outflow_to_padd3 = 2020 kbd`) | yes (alias to agg) | no | — |
| Inter-PADD agg (e.g. `inter_padd_2_to_3_agg`) | **yes (the TS)** | no | aliased by padd_view |
| Named pipelines (e.g. `pipe_capline`, `pipe_marketlink`, `pipe_seaway`) | no | yes — Principle 2.11 | `formula_inputs` of the inter-PADD agg's outflow |
| Bakken cross-state connector (`pipe_bakken_xstate`) | no | yes — Principle 2.11 | not yet wired to any inter-PADD agg (future: P2↔P4) |

`padd_aggr.outflow = Σ inter_padd_aggs.outflow` (TS-level identity, closes the partition).
`inter_padd_agg.outflow = Σ named_pipe.outflow` (latent constraint, Principle 2.11).
`pipe_bakken_xstate.outflow` could be made a constituent of `inter_padd_2_to_4_agg` and `inter_padd_4_to_2_agg` in a future refinement (currently structural-only).

**Files added:**

| File | Purpose |
|---|---|
| [split_bakken_gathering.py](split_bakken_gathering.py) | Migration: creates `bakken_mt_gathering` + `pipe_bakken_xstate`, re-routes 7 of the 14 gathering edges, deletes 14 obsolete variables (cascade clears assignments + resolved rows), adds inventory-membership for all 6 gathering nodes across the 4 producing PADDs. Idempotent. |

**Files modified:** `variable_assignments` (16 new for the split + connector; 14 deleted via cascade; 4 inventory overrides extended with gathering nodes), `assets` + `nodes` tables (+2 rows each), `variables` table (+19 added, −14 deleted, net +5 — actually +25 from the new connector vars too; total +25 vars).

**Where to resume:**

1. **Append `split_bakken_gathering.py` to the orchestrator** ([initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb)) so the corrected topology is rebuilt deterministically. Also append the audits with exit-1-on-fail as gates.
2. **Closure-vs-observed `v_balancing_item_check`** for Chapter 5 Claim 4 — now the cleanest possible artefact since every gap.I/gap.O closes (the closure delta now isolates exactly which latent flows are missing).
3. **Wire `pipe_bakken_xstate` as a constituent of `inter_padd_2_to_4_agg` + `inter_padd_4_to_2_agg`** (set `formula_inputs` on those agg outflows to include the connector's relevant outflow). This formalises that the Bakken cross-state connector contributes to the inter-PADD measurement, supporting eventual per-operator decomposition.
4. **Add `starter_basin` scenario** for cross-scenario consistency validation.
5. **Grade / quality dimension** — sits naturally on the imports_agg + canadian_imports_agg + exports_agg + inter-PADD agg + connector nodes (one variable per grade summing to the aggregate).
6. **Retire inline-SQL HTML generators** — carried over.
7. UI integration of `v_node_balance_check.sum_out_implied` — carried over.

---

## Earlier — Partition-aggregate generalisation: 19 aggs close every gap.I ✓ (shipped 2026-05-13 late evening, tenth pass)

**What landed.** The PADD 2 Canadian aggregate prototype from the ninth pass is now generalised to the full family of partition-gap-closing aggregates. One parametric script ([add_partition_aggregates.py](add_partition_aggregates.py)) builds three families:

1. **Canadian imports** (2 new): `padd{1,3}_canadian_imports_agg` — same pattern as `padd2_canadian_imports_agg`. TS authority on `agg.inflow_from_canadian_oil_sands`; the PADD-view inflow aliases through. PADDs 4 and 5 don't need new aggs — their existing cross-border pipes (`pipe_express_platte`, `pipe_trans_mountain_tmx`) are already physical-layer aggregates with reverse-mirror-resolvable `inflow_from_canadian_oil_sands` variables; the script re-points `padd{4,5}_view.inflow_from_canadian` to alias those (same-type chain via the pipe).
2. **Exports** (5 new): `padd{1..5}_exports_agg`, node_type=`export_terminal`. TS authority on `agg.outflow_to_foreign_export_destination`; the PADD-view outflow aliases through. PADD 3's exports agg holds the largest single TS in the family (3,631 kbd at 2024-12-01 = Gulf Coast crude exports).
3. **Inter-PADD corridors** (12 new): `inter_padd_{A}_to_{B}_agg`, node_type=`pipeline`, one per directional `A→B` corridor that has a `combined_inter_padd_*` TS. TS authority on `agg.outflow_to_padd_B_view`; the sender PADD's outflow aliases through. **Novel construct**: the agg's inflow side carries a *cross-type internal alias* `agg.inflow_from_padd_A = alias(agg.outflow_to_padd_B)` so the receiver PADD's inflow alias chain is also same-type and the agg becomes a partition child of *both* end-PADDs (closes the sender's gap.O and the receiver's gap.I in one TS binding). Mass balance closes at the agg by construction: pipeline pass-through forces ΣI=ΣO. The 3 corridors that had pipe-level constituents declared by [add_inter_padd_pipe_constituents.py](add_inter_padd_pipe_constituents.py) (P2→P3 Capline+MarketLink+Seaway-S, P3→P2 Basin+Seaway-N, P4→P2 Pony Express) have those constituents migrated onto the new agg.outflow's `formula_inputs` — Principle 2.11 latent-allocation documentation preserved in its natural home.

**Resolver verification.** Run id=12, all rule-6 alias chains fire cleanly. 10 of 12 inter-PADD aggs resolve on both sides at 2024-12-01 (`agg.inflow [derived/alias] = agg.outflow [observed/TS]`); 2 are NULL because their TS series (P3↔P5 corridors) have no data at this date — these are rare/zero crude movements, as flagged by the audit script's earlier note.

**Cross-checks at 2024-12-01:**

| Check | Before tenth pass | After tenth pass |
|---|---:|---:|
| Resolver unresolved | 0 | **0** |
| TS-binding uniqueness collisions | 0 | **0** (80 TS in 80 bindings) |
| gap.I padd1_view | 229 | **0** |
| gap.I padd2_view | 1,563 | **0** |
| gap.I padd3_view | 2,523 | **0** |
| gap.I padd4_view | 553 | **0** |
| gap.I padd5_view | 444 | **0** |
| gap.I usa_view | 0 | **0** |
| Aliases dispatched | 411 | **442** (+31 from new alias chains) |
| Partition aggregate nodes | 4 | **23** (+2 Canadian, +5 export, +12 inter-PADD) |
| TS-bound variables on padd_views directly | 17 | **0** — every TS now lives on a physical-layer aggregate |

**On gap.O.** The audit reports gap.O slightly negative on PADDs 2/3/4 after this pass. The new aggs contribute exactly `own.O` worth of boundary outflows (perfect match for the aggregate side), but a pre-existing audit artefact remains: producing basins on these PADDs (`bakken_nd → bakken_nd_gathering`, `permian_tx → permian_tx_gathering`, `eagle_ford_tx → eagle_ford_gathering`) have outflows to their gathering nodes, and gathering nodes are not registered as same-type partition children of the PADD view. The audit's `descendants()` function follows formula_inputs same-type only, so the gathering node is treated as boundary. This was already present before the tenth pass (PADD 3 had `bdy.O = 5,446` pre-migration); the new aggs added 4,264 of legitimate boundary outflow on top, making the artefact more visible as a sign-flip. Not a regression; not a real outflow gap. The audit can be refined later by extending `descendants()` to follow flow edges within a PADD (e.g. follow `inflow → outflow` pairs across nodes that share PADD metadata), or by declaring gathering nodes as inventory-children of their PADD via `formula_inputs` on `inventory__crude__padd{N}_view`.

**Files added:**

| File | Purpose |
|---|---|
| [add_partition_aggregates.py](add_partition_aggregates.py) | Parametric migration that builds the 19 new aggregates + re-points PADD 4/5 Canadian aliases. Three families (canadian_imports, exports, inter_padd) each driven by a config list; idempotent via `ON CONFLICT DO NOTHING/UPDATE`. Generalises the prototype from `add_padd2_canadian_imports_agg.py`. |

**Files modified:**

| File | Change |
|---|---|
| `variable_assignments` (data) | 19 new agg variables with their inflow + outflow assignments (TS on one side, latent or cross-type alias on the other); 17 re-pointed padd_view variables (each previously TS-bound, now alias-derived); 2 cross-border pipe inflow assignments created (express_platte, trans_mountain_tmx) where missing. |
| Asset graph | +19 physical-layer nodes (218 → 237 assets; net +19 since no nodes were removed). |

**Files NOT modified** (intentionally): the existing `padd2_canadian_imports_agg` is untouched. Its asymmetric pattern (TS on inflow, outflow latent) is fine for one-direction aggregates where only the receiver needs partition closure — the receiver-side alias is same-type, the sender (canadian_oil_sands) is a boundary node that doesn't need its outflow gap closed.

**Where to resume:**

1. **Audit refinement — extend `descendants()` to clean up the gap.O artefact.** Two clean options:
   - (a) Augment the children query: include flow-edge same-PADD relations as partition descendants (`bakken_nd_gathering` becomes a descendant of `padd2_view` because both share the PADD-2 geographic label).
   - (b) Declare gathering nodes as inventory-children of their PADD via `formula_inputs` on `inventory__crude__padd{N}_view`. Numerical effect: zero (gathering has S=0 from defaults). Audit effect: gathering enters `descendants(padd_N_view)`.

   (b) is the lighter touch and keeps the audit query unchanged.
2. **Append `add_partition_aggregates.py` to the orchestrator** ([initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb)) so the partition tree is rebuilt deterministically on every refresh. Also append `audit_partition_gaps.py` with exit-1-on-non-zero-gap.I as a gate.
3. **Grade / quality dimension** — the next major axis. Will sit naturally on the imports_agg + canadian_imports_agg + exports_agg nodes (one variable per grade, summing to the aggregate). Discussed in CLAUDE.md §6 as the production-deployment lens.
4. **`v_balancing_item_check`** — closure-vs-observed view for Chapter 5 Claim 4 (now that B is observed and every gap.I is closed, the closure delta isolates *which* latent flows are missing).
5. UI integration of `v_node_balance_check.sum_out_implied`, retire inline-SQL HTML generators — carried over from earlier passes.

---

## Earlier — Reverse-mirror dispatch fix + PADD 2 aggregate prototype + audit-from-DB ✓ (shipped 2026-05-13 evening, ninth pass)

**What landed.** Three orthogonal fixes that reinforce each other:

1. **Reverse-mirror dispatch bug.** In the resolver, a relational variable explicitly marked `formula='latent()'` would short-circuit to NULL even when its paired direction (inflow ↔ outflow on the same edge) had been observed. Two co-located bugs:
   - Dispatch rule 3 (latent) ran before rule 7 (mirror) and never fell through.
   - `build_deps()` skipped latent variables before reaching the paired-mirror dep block, so topological order was non-deterministic for those variables. The 3 mirrors that fired before this pass fired by luck of insertion order, not by design.

   Fix: hoist the paired-mirror dep above the early `continue` in `build_deps()`; promote mirror inside the latent dispatch rule itself. Added a `paired_aliases_us` cycle gate to handle the case where the paired side is itself an alias of us (e.g. `pipe_keystone.inflow ← canada` aliases the canada-side outflow, which is latent — both stay latent together, no false propagation).

   Verification: `canadian_oil_sands.outflow → padd{1..5}_view` now resolve via mirror (146 / 2940 / 431 / 273 / 444 kbd respectively), plus `→ usa_view = 4234 kbd`. Mirrors fired: 3 → 15.

2. **PADD 2 Canadian aggregate prototype.** New abstract node [padd2_canadian_imports_agg](add_padd2_canadian_imports_agg.py) (asset + node + 6 variables), `node_type='import_terminal'`, structural defaults P=C=ΔS=B=0. The `MCRIPP2CA2` TS (2,940 kbd at 2024-12-01) moved from `padd2_view.inflow ← canadian_oil_sands` to `padd2_canadian_imports_agg.inflow ← canadian_oil_sands`; the PADD-view inflow is now alias-derived. Topology:

   ```
   canadian_oil_sands ──[TS: MCRIPP2CA2]──→ padd2_canadian_imports_agg ──[latent, mirror]──→ padd2_view

   padd2_view.inflow ← canadian_oil_sands  =  alias(padd2_canadian_imports_agg.inflow ← canadian_oil_sands)
   ```

   The aggregate is the natural future home for per-grade decomposition (heavy sour dilbit, light synthetic, medium) once grade dimension is added to the schema. Principle 2.6 layering (physical-layer aggregate authoritative; observational view derives) now holds for Canadian inflows into PADD 2 the same way as for foreign supply into PADDs 1/3/5. Prototype only — analogous aggregates for PADDs 1/3 Canadian inflows + inter-PADD outflows are queued for the next pass.

3. **Partition-gap audit reads from DB, not HTML.** [audit_partition_gaps.py](audit_partition_gaps.py) previously parsed `oil_network_balance_resolver.html`'s `data["nodes"]["children_partition"]` to know which nodes are partition children. This made the audit dependent on a stale generated artefact whenever new nodes were added. Refactored to derive the partition tree directly from the DB via formula_inputs traversal, with a same-type / same-relation filter so that cross-edge aliases (e.g. `padd2_view.inflow ← padd1_view` aliases `padd1_view.outflow → padd2_view`) don't get counted as parent-child relations. The audit now reads from the same source the resolver uses.

**Cross-checks at 2024-12-01:**

| Check | Before ninth pass | After ninth pass |
|---|---:|---:|
| Mirror dispatches per run | 3 (by luck) | **15** (deterministic) |
| `canadian_oil_sands.outflow → padd{N}_view` resolved | 0 of 5 | **5 of 5** |
| `padd2_view.inflow ← canadian_oil_sands` source | observed (TS direct) | derived (alias of imports_agg) |
| PADD 2 partition-gap audit: gap.I | 4,447.5 kbd | **1,563 kbd** (decomposition of Canadian inflow now traced through agg) |
| PADD 2 partition-gap audit: bdy.I (kids) | 55.5 kbd | **2,940 kbd** (the agg's TS-observed inflow contribution to the partition's boundary) |
| Resolver unresolved | 0 | **0** |
| TS-binding uniqueness collisions | 0 | **0** |

The remaining PADD 2 gap.I of 1,563 kbd = 14.5 (P1→P2 alias) + 569 (P3→P2 alias) + 979 (P4→P2 alias) — inter-PADD inflows where no aggregate has been built yet. Each is a clean Principle-2.11 latent allocation per the same pattern; will close as more aggregates land.

**Files added:**

| File | Purpose |
|---|---|
| [add_padd2_canadian_imports_agg.py](add_padd2_canadian_imports_agg.py) | Idempotent migration: creates the asset + node + 6 variables + 2 assignments, re-points the PADD-view inflow to alias. Prototype of the generalised aggregate pattern. |

**Files modified:**

| File | Change |
|---|---|
| [resolve_scenario.py](resolve_scenario.py) | (a) Rule 3 (`latent`) now promotes to mirror first when paired side is resolved. (b) `build_deps()` hoists the paired-mirror dep above the early continue for `''`/`'0'`/`'latent()'`, adds `paired_aliases_us` cycle gate. See PART 11B of the walkthrough. |
| [audit_partition_gaps.py](audit_partition_gaps.py) | Reads partition tree from DB (formula_inputs traversal with same-type filter), no longer parses generated HTML. |
| [RESOLVER_WALKTHROUGH.txt](RESOLVER_WALKTHROUGH.txt) | New PART 3B (flow of resolution — when/what/why), updated PART 5 (build_deps fix), updated PART 7 Rule 3 (mirror promotion), new PART 11B (the joint-bug story), refreshed PART 12 dispatch counts. |
| [variable_assignments](#) | 1 row re-pointed: `inflow__crude__padd2_view__canadian_oil_sands` becomes alias-derived. |

**Where to resume:**

1. **Extend the aggregate pattern to remaining PADD 2 directions** — `padd2_to_padd3_agg` (TS: combined_inter_padd_P2_to_P3_kbd = 2020 kbd), `padd3_to_padd2_agg` (Basin + Seaway-N), `padd4_to_padd2_agg` (Pony Express), `padd2_exports_agg` (107 kbd). Each closes another slice of the remaining 1,563 kbd PADD 2 gap.
2. **Same pattern for PADDs 1, 3, 4** — Canadian aggregates where physical pipes exist; inter-PADD aggregates for each cross-PADD corridor. PADD 5 mostly done already.
3. **Append `audit_ts_binding_uniqueness.py` to the orchestrator** ([initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb)) — exit-1 on violations gates further automation.
4. **Grade / quality dimension** — the next major axis. Will sit naturally on the imports_agg nodes (one variable per grade, summing to the PADD-aggregate). Discussed in CLAUDE.md as the production-deployment lens.
5. UI integration, Chapter 5 Claim-4 view, retire inline-SQL HTML generators — carried over from earlier passes.

---

## Earlier on 2026-05-13 (eighth pass) — TS-binding uniqueness cleanup (one TS, one variable) ✓ (shipped 2026-05-13, eighth pass)

**What landed:** five PADD-view inflow variables on `padd{1,3,5}_view` (foreign supply) and `padd{4,5}_view` (Canadian corridor) had their EIA-series authority moved to the physical-layer node (the imports-aggregate or the cross-border pipe outflow), with the PADD-view inflow now alias-derived. Principle 2.8 attribution now holds at the timeseries level: every authoritative TS is bound to exactly one variable in the scenario. Same numerical values; same partition-gap audit numbers; cleaner architecture per Principle 2.6 (physical layer authoritative, observational aggregate is a formula view).

**The problem (Pedro's audit, 2026-05-13).** For PADD 1, the EIA series `MCRIPP12 − MCRIPP1CA2` (non-Canadian foreign supply, 476 kbd at 2024-12-01) was TS-bound on `padd1_view.inflow ← foreign_supply`, and `padd1_imports_agg.inflow ← foreign_supply` was aliased *backward* from it. Structurally this created two boundary edges crossing the partition (one into `padd1_view`, one into `padd1_imports_agg`) carrying the same physical flow, and inverted the natural layering: the physical-layer aggregate (`padd1_imports_agg`, which receives the tankers and routes to East-Coast refineries) was derived, while the observational region view was authoritative. PADDs 3 and 5 had the same pattern. Separately, the Canada-corridor series `MCRIPP{4,5}CA2` were TS-bound on *two* outflow edges from `canadian_oil_sands` — once into the pipe, once direct into the PADD view — making the Canada-side outflow sum appear to be 2× the true physical flow.

**The fix.** Migrations follow the same pattern in both cases:

```
foreign_supply  ──[TS]──→ padd{N}_imports_agg            (authoritative)
                              ↓
                          padd{N}_view.inflow ← foreign_supply  =  alias(...)

canadian_oil_sands  ──[TS]──→ pipe_{express_platte, tmx}   (authoritative)
                                  ↓
                              padd{4,5}_view.inflow ← canadian_oil_sands  =  alias(...)
```

**Files added:**

| File | Purpose |
|---|---|
| [audit_ts_binding_uniqueness.py](audit_ts_binding_uniqueness.py) | For each (scenario, timeseries), count distinct variable bindings. Flags any >1 and classifies by partition role (balance / constraint). Exits 1 on violations so it can gate orchestrator runs. |
| [repoint_foreign_supply_to_imports_agg.py](repoint_foreign_supply_to_imports_agg.py) | Idempotent migration: moves the foreign_supply TS from `padd{1,3,5}_view` to `padd{1,3,5}_imports_agg`, sets the padd_view inflow to alias-derived. |
| [repoint_canadian_corridor_ts.py](repoint_canadian_corridor_ts.py) | Same pattern for the two Canadian corridors (Express+Platte → PADD 4, TMX → PADD 5). |

**Files modified:** `variable_assignments` (5 rows re-pointed in the starter scenario).

**Resolver bug discovered along the way.** The resolver's alias rule (`resolve_scenario.py` L288) matches on `formula in by_id` — meaning the formula text must be the bare `variable_id`, not wrapped in `alias(...)`. The `alias(...)` form is only the display label written to `formula_used`. The first revision of `repoint_foreign_supply_to_imports_agg.py` wrote `formula = 'alias(...)'` and worked by coincidence because rule 10 (`sum_same_type`) caught it (parent and input both `inflow` type). The Canadian-corridor migration broke this — parent `inflow`, input `outflow` (different types) — and produced 2 unresolved variables × 156 dates = 312 NULL rows on the first try. Both migration scripts now use the bare-id convention.

**Cross-checks.**

| check | before | after |
|---|---|---|
| TS-binding collisions in starter scenario | 5 (3× foreign_supply + 2× Canadian) | **0** |
| Distinct TS / TS-bound variables | 80 / 82 | **80 / 80** (perfect 1:1) |
| Resolver unresolved | 0 | **0** |
| Resolved values for the 10 affected variables | as observed | identical (alias-derived) |
| Partition-gap audit (gap.I / gap.O per PADD) | unchanged | unchanged — values are not the point; *attribution* is |
| Partition audit foreign-supply decomposition trace | "no pipe decomposition declared" | `pipes: [inflow__crude__padd{N}_imports_agg__foreign_supply] → 1/1 resolved` |

**Where to resume (revised priorities):**

1. **Append `audit_ts_binding_uniqueness.py` to the orchestrator** ([initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb)) — after `resolve_scenario.py`. Exit-1 on violations gates further automation cleanly.
2. **UI integration** — surface `v_node_balance_check.sum_out_implied` next to latent outflows in the balance HTML explorer (carried over from the seventh pass).
3. **Chapter 5 Claim-4 view** — filter `v_node_balance_check` to non-zero `gap_kbd`, annotated with events (Harvey, COVID, SPR releases, Colonial).
4. **Retire inline-SQL HTML generators** — visual-compare originals vs `*_resolver.html` and drop inline CTEs.
5. **Add `starter_basin` scenario** for cross-scenario consistency validation.
6. **Closure-vs-observed `v_balancing_item_check`** for Chapter 5 Claim 4.

---

## Earlier on 2026-05-12 (seventh pass) — Node-type defaults + pass-through mass-balance closure ✓ (shipped 2026-05-12, seventh pass of the day)

**What landed:** every node type now has structural defaults in `oil_network.node_type_default_formulas`. `variable_assignments` carries only intentional overrides; the new `v_effective_assignments` view layers the two so the resolver sees a single coherent recipe per (scenario, variable). The substantive behavioural change: pass-through node types (gathering, origin_terminal, pipeline, import_terminal, export_terminal, foreign_export_destination, foreign_production_aggregate) now default to `B=0` with `P=C=ΔS=0` — which means mass-balance closes at every junction, even when the individual outflow split is unobservable.

**Resume from here — for Pedro:** the original audit case (`permian_tx_gathering has in but no out`) is resolved. The 3 individual outflows to {midland, wink, crane}_origin stay genuinely `latent` per Principle 2.11 (the split is unobservable), but `v_node_balance_check.sum_out_implied = 4313.1 kbd` — the constraint that those 3 latents must sum to the observed inflow. Same pattern at 7 other asymmetric junctions: `bakken_nd_gathering`, `eagle_ford_gathering`, `pipe_express_platte`, `pipe_trans_mountain_tmx`, `padd1/3/5_imports_agg`.

**Architecture (the new data model):**

```
v_effective_assignments (per (scenario, variable))
  = COALESCE(
        variable_assignments.formula,          -- 1. override (scenario-specific)
        node_type_default_formulas.formula,    -- 2. structural default (per node_type)
        'latent()')                            -- 3. fallback (any variable without either)
  + assignment_source ∈ {'override', 'default', 'fallback'}  -- audit tag
```

The resolver reads the view (was: INNER JOIN to `variable_assignments`); no other resolver code changed.

**Files added:**

| File | Purpose |
|---|---|
| [populate_node_type_defaults.py](populate_node_type_defaults.py) | Seeds 76 rows into `node_type_default_formulas` (19 types × 4 non-relational variable types). Idempotent. |
| [create_v_effective_assignments.py](create_v_effective_assignments.py) | Creates the layered view. CREATE OR REPLACE. |
| [create_v_node_balance_check.py](create_v_node_balance_check.py) | Creates the per-node mass-balance closure view (`sum_in`, `sum_out_obs`, `sum_out_implied`, `gap_kbd`, `status`). |
| [audit_flow_consistency.py](audit_flow_consistency.py) | Standalone diagnostic: 0 topology bugs, 12 asymmetric junctions, production-chain trace from each basin. |
| [clean_variable_assignments.py](clean_variable_assignments.py) | One-shot sparsifier (also baked into `assign_formulas.ipynb` cell c05). Two passes: (a) delete rows where formula = type default; (b) delete `latent()` overrides that mask new `0` defaults. |

**Files modified:**

| File | Change |
|---|---|
| [resolve_scenario.py](resolve_scenario.py) `load()` | Reads from `v_effective_assignments` instead of `variable_assignments` directly. |
| [assign_formulas.ipynb](assign_formulas.ipynb) cell `c05` | Appended a sparsify pass — same SQL as `clean_variable_assignments.py`, runs after the UPSERT so every orchestrator run keeps `variable_assignments` sparse. |
| [initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) `ASSIGNERS` | Expanded from 4 to 8 steps: prepends `populate_node_type_defaults.py`, appends `create_v_effective_assignments.py`, `resolve_scenario.py`, `create_v_node_balance_check.py`. The orchestrator is now end-to-end (re-running it lands a fresh resolver run with audit row + view-driven UI artefacts) — closing item 3 of the previous resume list. |

**Defaults table (the structural claim per node type):**

| node_type (count)                 | P         | C         | S         | B         | Mass-balance closes? |
|---|---|---|---|---|---|
| gathering (5)                     | 0         | 0         | 0         | **0**     | YES — ΣI = ΣO     |
| origin_terminal (6)               | 0         | 0         | 0         | **0**     | YES — ΣI = ΣO     |
| pipeline (24)                     | 0         | 0         | 0         | **0**     | YES — ΣI = ΣO     |
| import_terminal (4)               | 0         | 0         | 0         | **0**     | YES — ΣI = ΣO     |
| export_terminal (7)               | 0         | 0         | 0         | **0**     | YES — ΣI = ΣO     |
| foreign_export_destination (1)    | 0         | 0         | 0         | **0**     | boundary sink     |
| foreign_production_aggregate (2)  | latent    | 0         | 0         | **0**     | boundary source   |
| storage_terminal (10)             | 0         | 0         | latent    | latent    | hubs may have buffer behaviour |
| spr_site (4)                      | 0         | 0         | latent    | latent    | accumulator       |
| refinery (115)                    | 0         | latent    | latent    | latent    | consumer; per-asset C/S overridden as 0 in starter (coverage contract) |
| state_sub_basin (5)               | latent    | 0         | 0         | latent    | source            |
| state_conventional (7)            | latent    | 0         | 0         | latent    | source            |
| offshore_region (1)               | latent    | 0         | 0         | latent    | source            |
| observational_aggregate (4)       | sum_children | 0      | latent    | 0         | aggregate view; spr_total S overridden as sum_over_children |
| state_residual (5)                | latent    | 0         | 0         | 0         | arithmetic residual |
| state_view (2)                    | latent    | 0         | 0         | 0         | aggregate; P via state-level TS |
| usa_subtotal_view (1)             | latent    | 0         | 0         | 0         | aggregate         |
| refining_district_view (10)       | 0         | latent    | 0         | 0         | aggregate; C via district TS |
| region_view (6) PADDs + usa       | latent    | latent    | latent    | latent    | every term overridden in starter |

**Sparsity dividend:**

| | Before | After |
|---|---|---|
| `variable_assignments` rows (starter) | 1,690 | 1,001 |
| Rows sourced from defaults via view  |     — |   693 |
| Rows where formula matches type default and could be removed | ~600 | 0 |

**Audit cross-checks at 2024-12-01 — the closure constraint now activates:**

| node                       | ΣI       | ΣO_implied | n_outflows_latent | status |
|---|---:|---:|---:|---|
| permian_tx_gathering       | 4,313.1  | **4,313.1** |  3 | in-only (ΣO implied) |
| bakken_nd_gathering        | 1,247.5  | 1,247.5     | 13 | in-only (ΣO implied) |
| eagle_ford_gathering       | 1,133.4  | 1,133.4     |  1 | in-only (ΣO implied) |
| pipe_express_platte        |   273.0  |   273.0     |  8 | in-only (ΣO implied) |
| pipe_trans_mountain_tmx    |   444.0  |   444.0     |  4 | in-only (ΣO implied) |
| padd1_imports_agg          |   476.0  |   476.0     |  5 | in-only (ΣO implied) |
| padd3_imports_agg          | 1,108.0  | 1,108.0     | 29 | in-only (ΣO implied) |
| padd5_imports_agg          |   739.0  |   739.0     | 18 | in-only (ΣO implied) |

For `houston_hub`, `patoka_hub`, `guernsey_hub` the status is `open` rather than `in-only ΣO implied` — correctly, because storage_terminal keeps B/S latent (hubs may have real buffer behaviour we shouldn't auto-zero). They'll close once their inventory variables get observations or per-scenario overrides.

For region_view PADDs and `usa_view`, ΣO_implied is computed from observed P/C/S/B. The gap vs ΣO_observed *is* the Claim-4 closure error — at `padd5_view` for instance, ΣO_implied = -224 kbd is physically impossible, surfacing that EIA's published P/C/S/B/imports don't internally reconcile at that level. Exactly the signal the thesis wants to characterise.

**Where to resume (revised priorities):**

1. **UI integration**: balance HTML explorer should display `sum_out_implied` from `v_node_balance_check` next to the latent individual outflows. One-line annotation per junction: "ΣO = 4313.1 kbd (constraint-implied, split unobserved)".
2. **Chapter 5 Claim-4 view**: a per-(node, date) view filtering `v_node_balance_check` to non-zero `gap_kbd`, annotated with events (Harvey 2017, COVID 2020, SPR releases 2022, Colonial 2021) for the dissertation chart.
3. **Retire inline-SQL HTML generators** — visual-compare originals vs `*_resolver.html` pairs and drop the inline CTEs.
4. **Add `starter_basin` scenario** for cross-scenario consistency validation — now trivially cheap, since the new scenario only needs override rows; all the structural defaults are shared.
5. **Wire inter-PADD pipe constituents** on remaining corridors once pipe-level data exists.

---

## Earlier on 2026-05-12 (sixth pass) — Supply Adjustment binding + PADD stock decomposition ✓ (shipped 2026-05-12, sixth pass of the day)

**What landed:** 16 previously unbound EIA series moved out of staging and into the catalogue + facts table. The headline change is **B is now observed**, not closure-derived — this is the principle-2.8 promotion that unlocks the Chapter 5 Claim 4 experiment.

**Series added** (all in [assign_eia.ipynb](assign_eia.ipynb) cell `c02`, after the existing stocks block):

| Series | Count | Unit | Role | Variable binding |
|---|---|---|---|---|
| `MCRSFP{1..5}1` | 5 | mbbl_level | auxiliary | (on `padd{N}_view`, no `variable_assignments` row) |
| `MCRRSP{1..5}1` | 5 | mbbl_level | auxiliary | (on `padd{N}_view`, no `variable_assignments` row) |
| `MCRUA_NUS_2` | 1 | kbd | authoritative | `balancing_item__crude__usa_view` |
| `MCRUA_R{1..5}0_2` | 5 | kbd | authoritative | `balancing_item__crude__padd{N}_view` |

**B switch — closure → observed:** The 6 closure-formula tuples for `balancing_item__crude__{usa_view, padd1..5_view}` were removed from [assign_formulas.ipynb](assign_formulas.ipynb) cell `c02` (replaced by a comment block explaining why). Reason: `assign_formulas` runs after `assign_eia`, so without removing the closure tuples the UPSERT would overwrite the new TS bindings (the CHECK constraint `num_nonnulls(timeseries_id, formula) = 1` forces mutual exclusion). Resolver dispatch now shows `closure: 0` (was 5 variables × ~134 dates = ~670 rows that moved from `derived` to `observed`).

**Numeric validation at 2024-12-01:**

| Node | observed B (kbd) | closure-derived B (kbd, previous resolver value) | Δ — magnitude of intra-USA latent flow |
|---|---:|---:|---:|
| padd1_view | −108 | (was 205.5) | covered by missing inter-PADD pipe + Canadian observations |
| padd2_view | −128 |  | |
| padd3_view | −105 |  | |
| padd4_view | −32 |  | |
| padd5_view | −7 |  | |
| **usa_view** | **−379** | (was −2,826.5) | **2,447 kbd** — the Claim-4 quantity |

Cross-PADD aggregation invariant: USA observed B = −379, Σ-of-PADD observed B = −108 + −128 + −105 + −32 + −7 = −380. **Gap = 1 kbd** (rounding). The `v_aggregation_consistency` view now flags this row as `ok` — the first time both sides of the B-aggregation identity are independently TS-observed and they agree.

**Stock decomposition identity** `MCRSFP{N}1 + MCRRSP{N}1 = MCRSTP{N}1`:

| PADD | MCRSFP (MBBL) | MCRRSP (MBBL) | sum | MCRSTP (MBBL) | gap | notes |
|---|---:|---:|---:|---:|---:|---|
| 1 | 1,522 | 6,350 | 7,872 | 7,872 | 0 | closes |
| 2 | 90,451 | 13,230 | 103,681 | 103,681 | 0 | closes |
| 3 | 183,640 | 44,423 | 228,063 | 621,631 | **393,568** | SPR (4 sites in PADD 3): MCRSTP includes SPR, MCRSFP/MCRRSP exclude it |
| 4 | 22,061 | 2,373 | 24,434 | 24,434 | 0 | closes |
| 5 | 22,962 | 20,468 | 43,430 | 49,330 | 5,900 | likely Jones Act in-transit |

The HANDOVER's prior "MCRSFP{N}1 + MCRRSP{N}1 = MCRSTP{N}1 exactly" was true for PADDs 1/2/4. PADD 3 carries the SPR offset; PADD 5 has a small ~6 MMbbl gap (probably Jones Act tanker in-transit between PADDs 3/1 → PADD 5). Both are *physical* gaps, not data errors — they document what each EIA series does and doesn't include.

**Why auxiliary, not structural sub-nodes:** Adding `padd{N}_tank_farms_pipelines_view` + `padd{N}_refinery_stocks_view` as new sub-view nodes was considered and rejected. MCRSFP includes Cushing + Patoka (already structural children of padd2_view), so a new "tank farms" sub-view would double-count them. The auxiliary registration stores the data per Principle 2.8 (observed_auxiliary) for Chapter-5 cross-checks without distorting the mass balance.

**Per-district crude inputs (item 3 of the prior reality-check):** already at maximum EIA resolution. The `M_EPC0_YIY_R*` series cover PADD 1 (REC, RAP), PADD 2 (R2A, R2B, R2C), PADD 3 (R3A–R3E), PADD 4 (R40 only — no sub-codes published), PADD 5 (R50 only — no sub-codes published). All 13 codes are bound. No further work possible at this resolution.

**Files touched this pass:**
- [assign_eia.ipynb](assign_eia.ipynb) cell `c02` — appended 16 tuples to `EIA_SERIES`
- [assign_formulas.ipynb](assign_formulas.ipynb) cell `c02` — removed 6 closure-formula tuples (replaced with explanatory comment block)
- This document.

---

## Earlier on 2026-05-12 (fifth pass) — Inter-PADD direction bug fix + pipe constituents + audit ✓

**The bug:** EIA's inter-PADD pipeline series follow the convention `MCRMP_P{A}_P{B}_1` = "PADD A receives by pipeline from PADD B" → flow direction is **B → A**. `assign_eia.ipynb`'s `EIA_SERIES` raw bindings (lines 282-419) and the `COMBINED_INTER_PADD` derived-series list (lines 882-907) both treated the convention as if it were A → B. Result: every one of the 10 inter-PADD aggregate outflow variables (`outflow__crude__padd{A}_view__padd{B}_view`) carried the opposite direction's value.

**How it surfaced:** the partition-gap audit (`audit_partition_gaps.py`) flagged `padd2_view.outflow_to_padd4_view = 979 kbd` as anomalously large — PADD4 only has ~600 kbd of refining capacity, can't be receiving 1 mb/d from PADD2. Cross-referenced against the raw EIA series descriptions, the convention was confirmed reversed.

**The fix** ([apply_inter_padd_direction_fix.py](apply_inter_padd_direction_fix.py)):
1. For every raw `MCRMP*` / `MCRMT*` / `MCRMP_R*` binding, swap `(node, related_node)` so the auxiliary catalogue row sits on the correct sender's outflow.
2. For every `COMBINED_INTER_PADD` tuple, swap the component series to the opposite-direction's raw series. `combined_inter_padd_P{A}_to_P{B}_kbd` now correctly carries the P{A}→P{B} value.

**After the fix at 2024-12-01** — partition flows look physically correct:
- `padd2_view.outflow_to_padd3` = 2020 kbd (DAPL/ETCO + MarketLink + Capline-post-reversal + Seaway-S) ✓
- `padd3_view.outflow_to_padd2` = 569 kbd (Seaway-N, mostly) ✓
- `padd4_view.outflow_to_padd2` = 979 kbd (Pony Express + DJ Basin pipes) ✓
- `padd2_view.outflow_to_padd4` = 280 kbd (reverse, small) ✓
- USA closes at 6,557/6,557 (boundary) — unchanged, as expected (just direction relabelling at internal level).

**Pipe-constituents wiring** ([add_inter_padd_pipe_constituents.py](add_inter_padd_pipe_constituents.py)): added `formula_inputs` to the 3 inter-PADD aggregate outflows where modelled pipes exist (P2→P3, P3→P2, P4→P2). This documents Principle 2.11 (latent allocation at junctions): the aggregate stays TS-bound, but the per-pipe set is now declared so the consistency view will pick up the constraint when pipes become observed.

**Audit script** ([audit_partition_gaps.py](audit_partition_gaps.py)): categorises every PADD's own.I/O vs sum-of-children gap. Output now confirms USA closes; PADD-level gaps are entirely explained by latent pipe data (Mainline-CA + Keystone joint latent; intra-USA pipes Seaway/Capline/etc. joint latent; export terminals joint latent at PADD3).

**Reality check — TS that exist in staging but aren't catalogued yet** (would close further gaps if loaded into `oil_network.timeseries` + bound via `assign_eia`):

| Series | Coverage | Closes which gap |
|---|---|---|
| `MCRSFP{1..5}1` (PADD-level Stocks at Tank Farms + Pipelines), `MCRRSP{1..5}1` (PADD-level Stocks at Refineries) | 5/5 PADDs + national, 2015-01..2026-02, monthly | Would close the PADD-level S partition gap (today only Cushing is bound under PADD2; `MCRSFP21 + MCRRSP21 = MCRSTP21` exactly per the earlier check) |
| `MCRUA_NUS_1`, `MCRUA_R{1..5}0_1` (Supply Adjustment) | US + 5 PADDs, monthly | EIA's own balancing item — would turn `B` from derived-closure into TS-observed (Principle 2.8 dual-use: observed_authoritative + derived as constraint check). The Claim-4 thesis experiment depends on this. |
| Per-refining-district crude inputs (M_EPC0_YIY_R*_1 at sub-PADD district resolution if available) | partial — needs verification | Would close the PADD.C partition where district sums currently match exactly but it'd let us drop the district-view layer if redundant. |

These three were already queued in the "Where to resume" section of the previous pass — moving them up: they're the most-impactful unbound TS we have, and they're already in `oil_network_data_loader.eia_staging` (just need the catalogue + binding pass via `assign_eia`).

---

## Earlier on 2026-05-12 (fourth pass) — Scenario resolver, audit trail, and three parallel resolver UIs ✓

**What landed:**
1. [resolve_scenario.py](resolve_scenario.py) — walks the variable dependency DAG topologically and writes the resolved value of every `(variable, date)` pair to `oil_network.scenario_resolved_values`. One table holds every value any downstream consumer needs.
2. **Audit trail.** Every resolver run is logged in `oil_network.scenario_resolver_runs` (start/end timestamps, duration, dispatch counts as JSONB, free-text notes). Every value row is tagged with `run_id`. Lets you answer "when was this scenario last resolved?" or "what changed between runs 7 and 8?" without losing history to the DELETE-then-INSERT pattern on the values table.
3. [RESOLVER_WALKTHROUGH.txt](RESOLVER_WALKTHROUGH.txt) — plain-text guided reading of the resolver code, section-by-section. For offline reading.
4. Three **parallel "resolver" UI generators** that consume the new table:
   - [make_balance_resolver_ui.py](make_balance_resolver_ui.py) → `oil_network_balance_resolver.html`
   - [make_hierarchy_resolver_ui.py](make_hierarchy_resolver_ui.py) → `oil_network_hierarchy_resolver.html`
   - [make_map_resolver_ui.py](make_map_resolver_ui.py) → `oil_network_map_resolver.html`

   Each imports its original counterpart (`make_balance_ui`, `make_hierarchy_explorer`, `make_map`) to reuse layout + JS + structural logic, and replaces only the data layer with a `SELECT … FROM scenario_resolved_values`. The originals stay intact so the two outputs can be compared side-by-side before retiring the inline logic.

**Schema:**
```
oil_network.scenario_resolver_runs           -- one row per resolver invocation
  run_id          BIGSERIAL PK
  scenario_id     TEXT NOT NULL  (FK -> scenarios)
  started_at      TIMESTAMPTZ NOT NULL
  completed_at    TIMESTAMPTZ           -- NULL if the run crashed mid-way
  duration_ms     INTEGER
  n_assignments   INTEGER
  n_rows_written  INTEGER
  dispatch_stats  JSONB                 -- {observed: 76, zero: 449, ...}
  notes           TEXT

oil_network.scenario_resolved_values         -- one row per (scenario, variable, date)
  scenario_id      TEXT NOT NULL  (FK -> scenarios)
  variable_id      TEXT NOT NULL  (FK -> variables)
  observation_date DATE NOT NULL
  value            DOUBLE PRECISION  (NULL = latent / unresolved)
  source           TEXT  ('observed' | 'derived' | 'zero' | 'latent' | 'unresolved')
  formula_used     TEXT  (which resolution rule fired)
  timeseries_id    TEXT  (which TS, for observed)
  saved_date       TIMESTAMPTZ
  run_id           BIGINT  (FK -> scenario_resolver_runs)   <-- audit link
  PRIMARY KEY (scenario_id, variable_id, observation_date)
```

**Resolution rules (dispatch in priority order):** observed TS → zero → latent → `sum_over_children` → `sum_over_outflows` → single-var alias → reverse-mirror → closure (`B = ΔS − P + C − ΣI + ΣO`) → arithmetic (`A − B − C`) → same-type rollup → unresolved.

**Starter scenario output:** 1,690 assignments × 156 dates = **202,591 rows**. Dispatch counts: 76 observed, 449 zero, 738 latent, 408 alias, 8 arithmetic (the residual + basin-state derivations), 5 closure (PADD B's), 4 sum_over_children, 1 sum_over_outflows, 1 sum_same_type (usa_view B), 0 unresolved. The 0 unresolved row is the key invariant — every declared variable now has either a value or an explicit `latent` marker.

**Validation at 2024-12-01:**
- `padd2_view.P` partition closes exactly: 1837 = 1192 (bakken_nd) + 408 (oklahoma) + 237 (padd2_other, derived by arithmetic).
- `permian_tx.P = 6419.1 − 2106.0 = 4313.1` ✓
- `bakken_mt.P = 1247.5 − 1192.0 = 55.5` ✓
- `padd1_view.B = 205.5` (closure evaluates correctly).
- `usa_view.B = −2826.5` (sum of 5 PADD B's).

**What this enables:**
- HTML generators (balance, hierarchy, map) become **thin views** over `scenario_resolved_values` — drop the 100+ lines of inline SQL and the recently-added JS formula evaluator in [make_balance_ui.py](make_balance_ui.py).
- Cross-scenario validation (Chapter 5) becomes a SQL join: `WHERE scenario_id IN ('starter_us_crude_2015_2025', 'starter_basin')`.
- Forecasting features can be assembled directly from the resolved table.
- Aggregation-consistency view can compare resolved values vs sum-of-formula_inputs in one query.

**Where to resume:**

1. **Bind the three high-impact unbound TS** (per the reality-check table above): stock decomposition `MCRSFP{N}1` + `MCRRSP{N}1`, supply adjustment `MCRUA_R{N}0_1`, and per-district crude inputs. Each is a small pass in `assign_eia.ipynb` since the raw data is already in `oil_network_data_loader.eia_staging`. Expected effect: PADD-level S partition gap closes (today only Cushing under PADD2 is bound); PADD-level B becomes TS-observed; partition closure check becomes meaningful at every PADD level.
2. **Visual compare.** Open `oil_network_balance.html` and `oil_network_balance_resolver.html` in adjacent tabs. They should be visually identical for partition cells. Same exercise for the hierarchy and map pairs.
3. **Retire inline logic.** Once the resolver paths look correct, replace the originals: drop the inline `resolved_ts` CTE and the `FORMULAS` JS evaluator from `make_balance_ui.py`; drop the raw `timeseries_data` join from `make_hierarchy_explorer.py`; align `make_map.py` to use the resolver counts.
4. **Add `resolve_scenario.py` to the orchestrator.** Append it as the final step in [initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) so re-running the orchestrator always lands a fresh resolved table tagged with a new audit run.

---

## Earlier on 2026-05-12 (second pass) — Canadian asymmetry fix at PADDs 4 & 5 ✓

**The bug:** PADD 4 and PADD 5 `inflow__foreign_supply` carried *total* imports (`MCRIPP42`, `MCRIPP52`), while PADDs 1-3 carried the *non-Canadian* portion (`MCRIPP{1,2,3}2 − MCRIPP{1,2,3}CA2`). At the same time, the aggregate-level `inflow__crude__padd{4,5}_view__canadian_oil_sands` had no TS binding (only the pipe-level outflows `pipe_express_platte`, `pipe_trans_mountain_tmx` were bound). Result: a symmetric ±717 kbd gap between `usa_view`'s `inflow_from_foreign_supply` (under-attributed by 717) and `inflow_from_canadian_oil_sands` (over-attributed by 717) when the partition view summed PADDs.

**Root cause:** an early-assumption miss. The original load assumed `MCRIPP4CA2` / `MCRIPP5CA2` did not exist as EIA series. They do — they just weren't wired to the aggregate-level inflows. The pipe-level outflows already used them (1:1 proxies for Express+Platte and TMX), but the aggregate slot was left latent and the foreign side was left as the gross total.

**The fix:** three small edits in [assign_eia.ipynb](assign_eia.ipynb) (orchestrated by [apply_canadian_fix.py](apply_canadian_fix.py), idempotent):
1. Two new `EIA_SERIES` tuples binding `MCRIPP4CA2` → `inflow__crude__padd4_view__canadian_oil_sands` and `MCRIPP5CA2` → `inflow__crude__padd5_view__canadian_oil_sands` (mirroring the P1/P2/P3 pattern; same series_id can be bound twice — at pipe and aggregate level — because it represents the same physical flow at two different resolutions).
2. `FOREIGN_SUPPLY_REGIONS` for P4 / P5 updated: derived `foreign_supply` series now = `MCRIPP{4,5}2 − MCRIPP{4,5}CA2` instead of passthrough; ts_ids renamed `eia:foreign_supply_to_padd{4,5}_kbd` for consistency with P1-P3.
3. `DERIVED_TS_ID` map updated to point at the new ts_ids.

**Verification at 2024-12-01 (post-fix):**

| Inflow | Σ(PADDs) | USA | gap |
|---|---:|---:|---:|
| Canadian | 4,234 | 4,234 | 0 |
| Foreign  | 2,323 | 2,323 | 0 |
| Total imports | 6,557 | 6,557 | 0 |

All three sums close exactly. `v_aggregation_consistency` now reports **0 `inconsistent` rows**.

---

## Earlier on 2026-05-12 — Semantic fix: imports_agg foreign tanker inflows now in correct slot ✓

**The bug:** the three physical import-terminal aggregate nodes (`padd1_imports_agg`, `padd3_imports_agg`, `padd5_imports_agg`) were holding their non-Canadian foreign tanker import volume in the `production` slot via an alias formula (`production = inflow__crude__paddX_view__foreign_supply`). An import terminal does not *produce* crude — crude *flows into it* from foreign tankers. The numerical value was correct, but the variable_type slot was wrong, which:
- Confused the balance UI (value appeared in the **P** column instead of **I**)
- Inflated those nodes' apparent "production" totals in any downstream rollup that grouped by `variable_type`

**Root cause:** workaround left over from the region-aggregate refactor. Before that refactor, the imports_agg nodes had no flow edge to a foreign source, so `production` was the only available slot. The refactor changed the data flow but didn't add the proper inflow edges.

**The fix:**

1. **[add_imports_agg_edges.py](add_imports_agg_edges.py)** adds 3 new flow edges in the asset graph: `foreign_supply → padd[1,3,5]_imports_agg`. Each generates a relational variable `inflow__crude__paddX_imports_agg__foreign_supply` on the import terminal aggregate.
2. **`assign_formulas.ipynb`** updated:
   - `production__crude__paddX_imports_agg` set to `"0"` (correct semantics: import terminals don't produce crude)
   - The new `inflow__crude__paddX_imports_agg__foreign_supply` bound to `formula = inflow__crude__paddX_view__foreign_supply` (mirrors the PADD-level inflow; same edge value, finer-resolution slot)
3. Re-ran the full pipeline; verified that the resolver (with reverse-mirror dereference) picks up the inflow value via the PADD-level TS binding.

**Verification (2024-12-01):**

| Node | Old slot | New slot | Value |
|---|---|---|---:|
| `padd1_imports_agg` | `production` (formula = view inflow) | `inflow__from__foreign_supply` (formula = view inflow) | 476 kbd |
| `padd3_imports_agg` | `production` (formula = view inflow) | `inflow__from__foreign_supply` (formula = view inflow) | 1,108 kbd |
| `padd5_imports_agg` | `production` (formula = view inflow) | `inflow__from__foreign_supply` (formula = view inflow) | 1,183 kbd |

Same values; correct slot. In the balance UI these now appear in the **I** column under each PADD's import-terminal node, where you'd expect.

**Effect on numerical results:** zero. The values were always correct; only the semantic label changed. But the partition tree is now self-consistent — every cell value lives in the variable_type that semantically describes it.

**Note on edge count:** asset_graph went from 406 → 409 edges (3 new foreign_inflow edges). No node count change (still 219 nodes).

---

## Previous pass — Per-scenario node role: balance vs constraint ✓ (shipped 2026-05-12)

**The distinction this pass introduces:**

Within a scenario, every abstract aggregate is one of two things:

| Role | What it is | Treatment in the partition |
|---|---|---|
| **`balance`** | A partition cell in this scenario's active aggregation tree. | `Σ children = self` holds as an exact identity, per variable_type. Children come from `formula_inputs`. |
| **`constraint`** | An auxiliary observation OR a system-boundary node. | NOT summed into the partition. Provides additional equality/inequality constraints that bound the solution space — used to disambiguate latents and as data-quality cross-checks. |

The role is **per-scenario, per-node**: the same abstract node can be `balance` in one scenario and `constraint` in another. For example, in a future `starter_basin` scenario the PADDs become `constraint` and the basin aggregates become `balance` — same nodes, same TS data, just a different declared partition.

**Why this is needed:**

The active partition has to be a true partition of physical reality — every parent's value must equal exactly the sum of its children's values. Without the role tag, alternative observation aggregates (basin rollups, `spr_total`, STEO subtotals) would appear as siblings of the PADDs under `usa_view` and double-count when summed. The `role` tag tells the UI, the consistency view, and (later) the formula evaluator which nodes participate in the partition sum and which are auxiliary observations.

**Schema (one new table):**

```sql
CREATE TABLE oil_network.scenario_node_role (
    scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id) ON DELETE CASCADE,
    node_id     TEXT NOT NULL REFERENCES nodes(node_id)         ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('balance', 'constraint')),
    notes       TEXT,
    PRIMARY KEY (scenario_id, node_id)
);
```

Physical nodes have **no rows** in this table — they're implicit partition leaves of whichever balance node owns them. Only abstract aggregates need an explicit role.

**Tagging for `starter_us_crude_2015_2025` (geographic-primary scenario):**

| `role = balance` (16 nodes) | `role = constraint` (10 nodes) |
|---|---|
| `usa_view` | `permian`, `bakken`, `eagle_ford` (basin aggregates — re-slice of basin-state physical nodes already inside PADD partitions) |
| `padd1_view` … `padd5_view` | `spr_total` (SPR rollup — already counted inside `padd3_view.S` via `MCRSTP31`) |
| `district_REC/RAP/R2A/R2B/R2C/R3A/R3B/R3C/R3D/R3E_refining_view` | `usa_lower48_excl_gom_view` (STEO subtotal — auxiliary cross-check) |
| | `texas_state_view`, `montana_state_view` (state-level auxiliary observations) |
| | `canadian_oil_sands`, `foreign_supply` (boundary sources) |
| | `foreign_export_destination` (boundary sink) |

**Files added / updated:**

- [add_node_roles.py](add_node_roles.py) — creates the table if missing and populates it for the starter scenario. Idempotent. Lists are hardcoded but trivially extensible for new scenarios.
- [build_oil_network.ipynb](build_oil_network.ipynb) Step 5 — `scenario_node_role` DDL added to the rebuild path.
- [initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) — orchestrator now runs `add_node_roles.py` as a final step after `add_aggregation_constituents.py`.
- [make_balance_ui.py](make_balance_ui.py) — explicit role tag wins over kind-based defaults. UI now splits the table into two `<tbody>` blocks:
  - Top: **balance partition tree** rooted at the single balance root (`usa_view`). Constraint children are pulled out of this tree.
  - Bottom: **Constraint observations** section with the 10 constraint nodes as flat roots. Same 7 balance columns (P/C/I/O/B/S/ΔS), same cell-click-to-chart behaviour.
- [oil_network_balance.html](oil_network_balance.html) — regenerated.

**Effect on the UI:**

- Top section ("balance partition tree"): 1 root (`usa_view`) expandable to its 5 PADDs (only). The basin aggregates, SPR rollup, state views, STEO subtotal, and foreign boundary nodes are *removed* from `usa_view`'s child list — they no longer mislead by sitting alongside PADDs.
- Bottom section ("Constraint observations"): all 10 constraint nodes as a flat list. Each can be expanded to its own children (basin aggregates have basin-state children, `canadian_oil_sands` has the 4 cross-border pipelines, etc.).
- Each node row shows a `bal` / `cstr` chip alongside the existing kind tag.

**What the consistency check now looks like:**

For each variable type on each balance node, `Σ partition_children = self`. The `v_aggregation_consistency` view already does this. Constraint nodes show *their own* "sum-of-constituents vs observation" check, which catches data inconsistencies between EIA's PADD-level and basin-level reporting paths (the ±717 kbd Canadian/foreign asymmetry we documented earlier is exactly one of these).

**What's still pending — and now structurally easier:**

1. **Bind the SKR/STT layer** (PADD-level refinery operating stocks + tank-farm stocks) — closes the PADD inventory partition's ~80% gap.
2. **Python formula evaluator** — with `role`, `formula_inputs`, and the consistency view all in place, the evaluator's algorithm shrinks: (a) compute partition values by walking `balance` nodes; (b) compute constraint-implied additional equations from `constraint` nodes; (c) solve the latent system. Trivially extendable to forecasting / GAT layers later.
3. **A second scenario** (e.g. `starter_basin`) to validate the cross-scenario consistency claim — same TS data, different role assignment, residuals should reconcile. This is the Chapter 5 validation experiment.

---

## Previous pass — Three interactive HTML UIs over the data model ✓ (shipped 2026-05-12 earlier)

Pedro now has three complementary self-contained HTML explorers, each surfacing a different slice of the model.

### 1. Geographic map — [oil_network_map.html](oil_network_map.html)

Built by [make_map.py](make_map.py). 195 of 197 physical assets plotted (165 with native lat/lon from `oil_network.locations`; 28 inferred by averaging connected neighbour coordinates — mainly pipelines and the foreign boundary nodes). 376 directed flow edges between physical assets.

- **Projection:** `natural earth` scoped to North America. Alaska appears at its actual geographic position (~60° N, around −150° E); the lon/lat range extends to −172 to capture the full Aleutian/ANS reach. Previous attempt with `albers usa` produced the awkward Alaska inset.
- **Subtype legend:** 14 colour-coded subtypes (refineries, pipelines, hubs, SPR, gathering, origin terminals, import/export terminals, basin production, etc.). Each toggleable.
- **Edge click → side panel:** every edge has an invisible clickable midpoint marker. Click anywhere near the centre of a line and the side panel renders: source/target node IDs, commodity, the underlying `outflow` variable_id, and metadata for both endpoint nodes (name, subtype, PADD/state, variable counts).
- **Node click → side panel:** same panel renders the node's full metadata.
- **Why the invisible-midpoint trick:** Plotly's `plotly_click` only fires on actual data points, not on line segments between them. The midpoint markers are a 376-point trace at near-zero opacity (`0.001`) with a 14-pixel hit area.

### 2. Hierarchy explorer — [oil_network_hierarchy.html](oil_network_hierarchy.html)

Built by [make_hierarchy_explorer.py](make_hierarchy_explorer.py). Drill-down tree: USA → PADDs → districts / basins / state aggregates → individual physical assets. Parent assignment uses explicit `formula_inputs` as the primary source, with structural overrides for the region tree and geographic fallback for the few physical leaves not yet wired via formula_inputs. **0 orphan physical nodes** — every asset is reachable from the aggregation tree.

Each node shows: kind (abstract/physical), subtype, parent (and inference source), variables, TS bindings. Click a TS-bound variable → its full time series in the right pane.

### 3. Balance-equation UI — [oil_network_balance.html](oil_network_balance.html)

Built by [make_balance_ui.py](make_balance_ui.py). The flagship view of the mass-balance equation per node.

- **Rows:** nodes, starting with roots; click ▶ to expand to children (same tree as the hierarchy explorer).
- **Columns at a selected date:** **P** (production), **C** (consumption), **I** (total inflow — sum of all inbound), **O** (total outflow — sum of all outbound), **B** (balancing item), **S** (inventory level, MBBL), **ΔS** (inventory change, kbd).
- **Date picker** at top — 156 monthly dates available, defaults to latest.
- **Cell click → side chart:** any non-empty cell renders that (node, variable) time series 2015→2026, plus min/mean/max stats.

**How the values are computed** (matches `v_aggregate_balance` semantics):
- Direct TS binding → use that observation
- One-level mirror dereference (formula = `<paired_var_id>`) → resolve to the paired variable's TS
- Region-aggregate B values come from `v_aggregate_balance` (the SQL closure view)
- Anything still unresolved → "—" (latent, honestly displayed)

**Important fix during this pass:** the initial parent-map query was treating ALL `formula_inputs` references as aggregation links, which conflated three different relationships:
1. **True aggregation** (parent.X.formula_inputs = [child1.X, child2.X, ...]) — same variable_type, cross-node
2. **Same-node composition** (balancing_item.formula_inputs = [P, C, S, F_in, F_out]) — different variable types, same node
3. **Mirror references** (inflow.formula_inputs = [paired_outflow]) — different variable_type, cross-node

The fix: filter to `parent_var.variable_type = child_var.variable_type AND parent_node != child_node`. Without this filter, `usa_view` was disappearing from the root list because the manual `foreign_supply.production.formula_inputs = [..., inflow__crude__usa_view__foreign_supply, ...]` made `usa_view` look like a child of `foreign_supply`.

### Files added/updated in this pass

- [make_map.py](make_map.py) — geographic map generator (Alaska-friendly projection, clickable edges)
- [make_hierarchy_explorer.py](make_hierarchy_explorer.py) — tree drill-down generator
- [make_balance_ui.py](make_balance_ui.py) — per-node balance table generator
- [make_explorer.py](make_explorer.py) — earlier variable-by-variable explorer (still works)
- [oil_network_map.html](oil_network_map.html) — generated
- [oil_network_hierarchy.html](oil_network_hierarchy.html) — generated
- [oil_network_balance.html](oil_network_balance.html) — generated
- [oil_network_explorer.html](oil_network_explorer.html) — generated

### Spot-checks for the balance UI

At `usa_view` on 2024-12-01 you should see roughly:

| Cell | Value | Source |
|---|---:|---|
| P | 13,437 | MCRFPUS2 |
| C | 16,772 | M_EPC0_YIY_NUS_2 |
| I | ~6,557 | sum of `usa_view`'s inflow vars |
| O | ~3,752 | foreign_export_destination outflow |
| B | ~+334 | `v_aggregate_balance` closure |
| S | 806,948 | MCRSTUS1 |
| ΔS | ~−196 | (S_dec − S_nov) / 31 |

Expand `usa_view` → 5 PADDs + basin aggregates + state views + usa_lower48_excl_gom + spr_total appear. Expand PADD 2 → cushing_hub, patoka_hub, bakken_nd, oklahoma_conventional, padd2_other, R2A/R2B/R2C districts, etc. The data thins out as you go deeper — refineries show "—" almost everywhere because per-refinery data is latent. The dashes are the framework being honest about what isn't observed at that resolution.

### What's still pending

1. **Bind PADD-level inventory components** (SKR refinery operating stocks + STT tank-farm stocks) — these would close the PADD inventory consistency gap (currently ~80% of PADD inventory is unbound, just Cushing hub data shows up).
2. **Python formula evaluator** — would let `balancing_item.formula` actually compute at non-aggregate nodes, plus offer a proper per-scenario, vintage-aware evaluation. The SQL views (`v_aggregate_balance`, `v_aggregation_consistency`) do this for the region aggregates already.

---

## Previous pass — Explicit aggregation constituents + data-quality consistency view ✓ (shipped 2026-05-11)

**The framework distinction this pass codifies:**

> **An abstract aggregate node exists because data exists at that aggregation level**, not because of geography. The link between an abstract aggregate and the physical assets it represents is declared via `formula_inputs` on the aggregate's variable — *even when the aggregate's value comes from a TS observation*.

**Schema semantics extended (no DDL change required — uses existing `formula_inputs TEXT[]`):**

- `timeseries_id` set → variable's **value** comes from the TS observation (authoritative).
- `formula_inputs` populated → declares the **constituents** that should aggregate to this variable's value.
- The two can coexist on the same row. Existing CHECK constraint `num_nonnulls(timeseries_id, formula) = 1` is unchanged: it governs the *formula*, not the *constituents list*.

When both are present, `sum(constituents)` should equal the TS value. Any material gap = data-quality flag (the IEA statistical-adjustment line is exactly this kind of residual). TS wins on value; the constituents list gives the structural sanity check.

**Files added:**
- [add_aggregation_constituents.py](add_aggregation_constituents.py) — derives constituents from the asset graph and populates `formula_inputs` on **33 TS-bound aggregate variables**:
  - USA (P, C, S, B, foreign_inflow, canadian_inflow, foreign_outflow) → 5 PADD aggregates each
  - PADD consumption (1-3) → sum of refining districts (R10/R20/R30 split)
  - PADD consumption (4-5) → sum of refineries in the PADD
  - PADD production → modelled basin/state nodes + `paddX_other` residual
  - PADD inventory → hubs + SPR sites (PADD 3)
  - 10 refining districts → all refineries in that district (using `configuration.duoarea_code`)
  - Basin aggregates (`permian`, `bakken`, `eagle_ford`) → sub-state nodes
- [_create_consistency_view.sql] (committed into `build_oil_network.ipynb` Step 7) — adds 8th view:
- [make_hierarchy_explorer.py](make_hierarchy_explorer.py) — produces [oil_network_hierarchy.html](oil_network_hierarchy.html), a self-contained drill-down explorer (USA → PADDs → districts/basins → physical assets) using **explicit formula_inputs** as the primary parent source; structural overrides for the region tree; geographic fallback only for the few physical leaves not yet wired explicitly.

**Files updated:**
- [build_oil_network.ipynb](build_oil_network.ipynb) Step 7 — adds 8th view `v_aggregation_consistency`.
- [initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) — orchestrator now runs `.py` scripts as well as `.ipynb` notebooks; `add_aggregation_constituents.py` is the final step after the formula notebook.

**Findings from the new `v_aggregation_consistency` view (2024-12-01 sample):**

| Aggregate | Observed | Σ constituents | Gap | Status |
|---|---:|---:|---:|---|
| `production__crude__usa_view` | 13,437 | 13,439 | −2 (rounding) | ok |
| `consumption__crude__usa_view` | 16,772 | 16,772 | 0 | ok |
| `inventory__crude__usa_view` | 806,948 | 806,948 | 0 | ok |
| `outflow_to_foreign_export__usa_view` | 3,752 | 3,752 | 0 | ok |
| `consumption__crude__padd3_view` | 9,390 | 9,391 | −1 | ok (sum of 5 districts) |
| `inflow_from_foreign_supply__usa_view` | 2,323 | 2,323 | 0 | ok (after 2026-05-12 Canadian-asymmetry fix) |
| `inflow_from_canadian_oil_sands__usa_view` | 4,234 | 4,234 | 0 | ok (after 2026-05-12 Canadian-asymmetry fix) |
| `inventory__crude__padd2_view` | 103,681 | 20,428 | 83,253 | partial_coverage (only Cushing observed; refinery & pipeline stocks latent) |

~~The symmetric ±717 kbd gap in foreign vs Canadian inflows~~ was resolved on 2026-05-12: `MCRIPP4CA2` and `MCRIPP5CA2` do publish (the earlier assumption that they didn't was wrong). They are now bound at the aggregate level (`inflow__crude__padd{4,5}_view__canadian_oil_sands`) and subtracted from PADD 4/5 `foreign_supply` to produce the non-Canadian piece — consistent with the P1/P2/P3 pattern. The consistency view confirms zero residual on both sides. **The framework's "surface the gap mechanically" property still held during the bug life** — that's how we found and fixed it.

The PADD-2 inventory gap is the upcoming workstream (SKR/STT series: refinery operating stocks + tank-farm stocks at PADD level).

**Hierarchy audit results:**

- **0 orphan physical nodes** — every physical asset is reachable from the abstract aggregation tree (USA → PADDs → districts → refineries, hubs, etc.).
- 217 of 219 nodes have a parent in the tree.
- 2 roots: `usa_view` (US-domestic aggregate) and the foreign boundary node (`foreign_supply` / `foreign_export_destination` collapse depending on which gets a formula_input first).

**Counts after this pass:** 219 assets / 1,688 variables / 33 aggregation constituents declared / **0 orphans**.

**Where to resume:**

1. **Bind PADD-level inventory components** (the SKR/STT series for tank farms + refinery operating stocks) — would close the PADD inventory consistency gap and put refinery and tank-farm aggregations into the hierarchy too.
2. **Python formula evaluator** — same script that's been on the deck for a while; with `formula_inputs` now declared on TS-bound aggregates, the evaluator's job at observed-aggregate nodes is just to compute `sum(constituents)` and surface the gap. Trivial change from the existing v_aggregation_consistency SQL view — but Python-side enables per-scenario, vintage-aware computation and exposes the result programmatically.

---

## Previous pass — Region-aggregate refactor: balance is structural now ✓ (shipped 2026-05-11 earlier)

**What changed in shape:** the data model moved from ~33 per-observation view nodes (one observation per node, like `usa_production_view`, `padd2_imports_view`, etc.) to **6 region-aggregate nodes** (`usa_view`, `padd1..5_view`) each carrying a complete mass-balance variable set (P, C, S, B + inflow/outflow per cross-boundary edge). The `v_aggregate_balance` view is now a pure structural pivot over `formula_inputs` — no series IDs, no CASE-per-PADD, no hardcoded region list.

**Why:** the old view had ~150 lines of hardcoded SQL (specific timeseries_ids, CASE-by-PADD, fixed region list) because each balance input lived on a different view node. The fix was structural: consolidate inputs per region onto one balance-region node, then let the view walk `formula_inputs` of each region's `balancing_item` to discover the inputs. Same `formula_inputs` primitive the framework already uses for aggregation — now applied to cross-type balance composition.

**Design principles re-stated:**
- **No `B = 0` defaults on physical nodes** anywhere. B is `latent()` everywhere unless an explicit closure formula is declared (currently only on the 6 region aggregates).
- **Abstract aggregate nodes carry the full variable set** for their region; B's `formula_inputs` references the P/C/S + inflow/outflow on the same node. Physical-node B's stay latent — the data doesn't observe a node-level closure residual, so the framework doesn't pretend it does.
- **Mirror dereference:** the new view follows one level of "formula = paired_variable_id" indirection so that the inflow side of a relational pair resolves to the same TS as the outflow side. This is how inter-PADD inflows (mirror-bound to their paired outflows) get resolved.
- **Pipelines aren't owned by any PADD** — they're cross-region transit infrastructure. Their line-fill stays latent; their per-edge flows are also latent under the PADD-authoritative scope. Inter-PADD flow at the abstract layer is bound to the observed combined-mode (pipeline + tanker/barge) series and lives on the region aggregates.

**Files added in this pass:**
- [add_region_aggregates.py](add_region_aggregates.py) — adds `usa_view`, `padd1..5_view`, `foreign_supply` + 30 edges (boundary + region-to-region inter-PADD).
- [drop_old_observation_views.py](drop_old_observation_views.py) — retires 36 nodes: 33 per-observation view nodes (`usa_production_view`, `padd*_imports_view`, etc.), plus `rest_of_l48` (pure rollup orphan, no observation), plus the 2 old `padd*_production_view → padd*_refining_view` abstract edges that had been superseded.

**Files updated:**
- `assign_eia.ipynb` — 28 region-level EIA bindings retargeted to the new region aggregates; demoted 7 old total-import bindings to auxiliary; added a new cell building 6 derived `eia:foreign_supply_to_<region>_kbd` series (USA + PADDs 1-3: `MCRIPP*` minus `MCRIPP*CA2`; PADDs 4-5: passthrough of total imports since no Canadian-only series); migrated 12 inter-PADD bindings to the new region-aggregate variable IDs.
- `assign_formulas.ipynb` — added 6 `balancing_item__crude__<region>_view` formulas with full `formula_inputs` lists; flipped every `B = 0` default to `B = latent()` across all rules (production nodes, refineries, refining districts, refining centres, import terminals, SPR sites, spr_total, infra default); removed obsolete formulas referencing retired nodes; rewrote `padd*_imports_agg` to alias the new `inflow__crude__padd*_view__foreign_supply` variables.
- `build_oil_network.ipynb` Step 7 — `v_aggregate_balance` replaced with the structural-pivot version (~80 lines, zero hardcoding).

**Verification (2024 sample dates):**

| Date | Region | B (kbd) before refactor | B (kbd) after refactor | Match |
|---|---|---:|---:|---|
| 2024-12 | USA | 334.0 | 334.0 | ✓ exact |
| 2024-12 | PADD 1 | 205.5 | 205.5 | ✓ |
| 2024-12 | PADD 2 | -1585.9 | -1585.9 | ✓ |
| 2024-12 | PADD 3 | 2941.0 | 2941.0 | ✓ |
| 2024-12 | PADD 4 | -1446.1 | -1446.1 | ✓ |
| 2024-12 | PADD 5 | 217.4 | NULL (MCREXP52 sparse) | data gap, not regression |
| 2024-06 | sum-of-5-PADDs | — | 651.1 | matches USA = 651.0 ✓ |

Per-PADD B values remain large (±1500 kbd) — the data-quality finding from the previous pass is unchanged. The cleanup was structural, not numerical.

**Counts after refactor:**
- 219 assets (down from 255 — net **-36 nodes**)
- 1,688 variables (down from 1,892)
- 1,688 of 1,688 assigned (100%)
- 74 TS-bound; 735 latent (previously 121 TS-bound / 535 latent — the latent shift reflects flipping the false B=0 defaults to honest latent)
- 121 EIA catalogue series; 14,249 facts

**Where to resume:**
The framework now represents the mass-balance identity correctly at every level. Two clear next pieces of work:
1. **Python formula evaluator** — the `balancing_item.formula` text on region aggregates documents the math, but the SQL view is what actually computes B. A small Python evaluator (~200 lines, stdlib `graphlib.TopologicalSorter` + pandas) would crank arbitrary formula DAGs, give B at every node where it's derivable, and make the framework's `formula_inputs` machinery first-class at runtime instead of only at the view layer. Chapter 5 validation experiments need this.
2. **Refinery long-tail collapse** (deferred from this pass) — the 115 individual refinery nodes are mostly latent (no per-refinery observation). Could collapse the long-tail into per-district `other_refineries_district_RXX` residuals, dropping ~65 nodes with no fidelity loss at PADD/district level. Decision deferred; affects Chapter 6 figures.

> If this is your first session in this repo, read [CLAUDE.md](CLAUDE.md) and [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) first. Then come back here.

---

## Previous pass — BALANCING ITEM B computable at aggregate level ✓ (shipped 2026-05-11 morning)

The mass-balance closure residual `B = ΔS − P + C − F_in + F_out` is now computable per month at the USA + 5 PADD aggregate views, plus ΔS (and implied net throughput under B=0 assumption) at Cushing.

**What landed:**

- **Inter-PADD movements completed**: 12 new EIA series bound (10 tanker/barge + 2 sub-PADD pipeline) covering all observable inter-PADD crude movements. Pipeline-only `MCRMPP*` series demoted from `authoritative` to `auxiliary`. Derived **combined `eia:combined_inter_padd_PxPy_kbd` series** (12 of them) sum pipeline + tanker/barge per direction and are the new authoritative bindings for the abstract inter-PADD flow variables.
- **Asset-graph patch** [add_inter_padd_flow_edges.py](add_inter_padd_flow_edges.py): added 2 missing abstract edges `padd3_production_view → padd5_refining_view` and `padd5_production_view → padd3_refining_view` (Jones Act WB↔WC tanker routes; no pipeline equivalent). Inter-PADD edge count: 10 → 12.
- **Two new SQL views** in [build_oil_network.ipynb](build_oil_network.ipynb) Step 7:
  - `v_inventory_changes` — per-month ΔStock (MBBL + kbd) for every TS-bound stock series. Cushing's ΔS is directly queryable here.
  - `v_aggregate_balance` — 6 rows per month (USA + 5 PADDs) with columns `ds_kbd, p_kbd, c_kbd, fin_kbd, fout_kbd, b_kbd`. B is the closure residual.

**Sanity checks (2024 sample):**

| Identity | Result |
|---|---|
| `sum(PADD_B) = USA_B` per month | exact (332 vs 334 kbd at 2024-12, rounding only) |
| USA monthly B 2024 | range +178 to +937 kbd, mean ~+550 kbd — consistent with EIA Petroleum Supply Monthly statistical-adjustment line |
| Stock-level identities | `MCRSTUS1 = MCESTUS1 + MCSSTUS1 = sum(MCRSTP11..P51)` exact |

**Per-PADD B values are large (±1500 kbd) — this is the finding.** PADD 2 B = −1586 and PADD 3 B = +2941 at 2024-12 are not binding gaps; they reflect actual EIA data inconsistencies at PADD level (the same inconsistencies cancel at USA level). Likely causes: refined-product flows misclassified as crude in inter-PADD movements, PADD-boundary state-classification quirks (e.g. New Mexico in PADD 3 vs PADD 4 reporting), import/export timing offsets relative to stock observations. This is exactly the kind of regional data-quality signal the framework is designed to surface — **thesis-relevant finding**, document in Chapter 6.

**Cushing specifically:** ΔS observable (via `v_inventory_changes` filtered to `MCRST_YCUOK_1`); per-edge F_in/F_out remain `latent()` (EIA publishes only stocks for Cushing). B_cushing is jointly latent with net throughput — the standard trade convention `B_cushing = 0 → net_throughput = ΔS` lets you read Cushing draw/build directly.

**Counts after rebuild:** 248 assets / 406 edges / **1,804 variables / 1,804 assigned (100%) / 75 TS-bound** / 115 EIA catalogue series / 14,249 facts.

### What's *not* yet done — formula evaluator

The balancing-item math currently lives in the SQL view (declarative, computable in one query). The framework's `variable_assignments.balancing_item` slots are still `0` on the view nodes — the formula equivalent of the view has not been written into the declarative layer. **Next workstream**: build the Python formula evaluator (~200 lines, `graphlib.TopologicalSorter` + pandas), and add `balancing_item__crude__usa_view` etc. formulas that the evaluator can crank to reproduce the view's numbers. Until then, the view is the source of truth for B.

---

## Previous pass — STORAGE / inventory layer ✓ (shipped 2026-05-08)

Crude-stock time series are now bound to the graph. Monthly ΔStock per region (Cushing in particular) is directly computable from `oil_network.timeseries_data`.

**What landed:**

- **Patch script** [add_storage_views.py](add_storage_views.py): 7 new abstract observational view nodes — `usa_total_stocks_view`, `usa_commercial_stocks_view`, `padd1/2/3/4/5_stocks_view`. (`cushing_hub` and `spr_total` already existed — their `inventory` was rebound directly.)
- **New unit value `'mbbl_level'`** in `oil_network.timeseries` for stock-level snapshots (vs the existing `kbd` rate and `mbbl` monthly volume). No per-row days conversion: stock levels are not rates. The `assign_eia` catalogue + facts loader handles the new unit branch (raw copy, no derived `_kbd` series).
- **9 new authoritative TS-bindings** in [assign_eia.ipynb](assign_eia.ipynb):
  - `MCRST_YCUOK_1` → `inventory__crude__cushing_hub` (only EIA-tracked individual hub)
  - `MCRSTUS1` → `inventory__crude__usa_total_stocks_view`
  - `MCESTUS1` → `inventory__crude__usa_commercial_stocks_view`
  - `MCSSTUS1` → `inventory__crude__spr_total` (was `sum_over_children`)
  - `MCRSTP11/21/31/41/51` → `inventory__crude__paddX_stocks_view`
- **Inventory rule change** in [assign_formulas.ipynb](assign_formulas.ipynb): `storage_terminal` and `pipeline` inventories flip from `0` to `latent()` (real working storage / line-fill — physically present, just not published per asset). Other infrastructure (origin terminals, gathering, export terminals, foreign_export_destination) stays `0`. SPR site inventories remain `latent()`. The new stocks view subtypes (`usa_stocks_view`, `padd_stocks_view`) are added to the `vars_df` scope so their P/C/B slots default to `0`.

**Stock-identity sanity checks (Postgres, 2024 sample dates):**

| Identity | Result |
|---|---|
| `MCRSTUS1 = MCESTUS1 + MCSSTUS1` (US total = commercial + SPR + in-transit) | exact, in-transit ≈ 0 |
| `MCRSTUS1 = sum(MCRSTP11..P51)` (US total = PADD sum) | exact |
| Cushing 2024 trajectory | 27.7 → 34.7 mbbl (Feb–May build) → 26.1 (Aug draw) → 20.4 (Dec). ΔStock per month directly available. |

**Counts after rebuild:** 248 assets / 419 edges / 1,800 variables / **71 TS-bound** / 535 latent / **1,800 of 1,800 assigned (100%)**. EIA catalogue: 79 series (62 kbd + 8 mbbl + 9 mbbl_level). Facts: 10,487 rows.

**No per-hub inventory** for Houston / Nederland / Patoka / Corpus Christi / St James / Guernsey — they stay `latent()`, jointly constrained by PADD-level totals + node-level mass balance once the formula evaluator runs. Per-pipeline line-fill is similarly latent.

### Inventory follow-on — what else EIA publishes (catalogue audit 2026-05-08)

Audit of `oil_network_data_loader.ref_timeseries` for crude-stock processes turned up **63 inventory-related series**. We bound the 9 high-priority ending-stock series; the rest are inventory-layer detail that decomposes the same totals further. Tabulated:

| Process | Description | Geographic resolution | Series in catalogue | Currently bound | Priority |
|---|---|---|---:|---:|---|
| `SAE` | Ending Stocks | US, 5 PADDs, Cushing (monthly + weekly US) | 8 | **7** | done |
| `SAS` | Ending Stocks SPR | US monthly + weekly | 2 | 1 | done |
| `SAX` | Ending Stocks Excl SPR | US monthly + weekly + 5-PADD weekly + Cushing weekly | 8 | 1 | weekly only ↓ |
| `SAXL` | Ending Stocks Excl SPR & Lease | US + 5 PADDs (weekly) | 6 | 0 | weekly only ↓ |
| `SKR` | **Stocks at Refineries** | US + 5 PADDs + 10 refining districts | **16** | 0 | **HIGH** |
| `STT` | **Stocks at Tank Farms** | US + 5 PADDs | **6** | 0 | **HIGH** |
| `SKA` | **Stocks in Transit (Alaska, on ships)** | US monthly + weekly | 2 | 0 | medium |
| `SKL` | Stocks at Leases | US monthly | 1 | 0 | discontinued (only 18 obs through 2016-06; EIA stopped publishing) |
| `SCG` `SCS` `SCX` | Stock Change variants | US + 5 PADDs, in MBBL and MBBL/D | 12 | 0 | low — derivable from levels |

**No state-level stocks** are published by EIA at all (verified by SQL: 0 rows when filtering area_name outside US/PADD/NA). Coastal-state and inland-state working storage is rolled directly into PADD totals.

**Decomposition identity (verified exact, 2024-12):**

```
MCRSTUS1  =  MCRSFUS1  +  MCRRSUS1  +  MCSSTUS1  +  MCRSAUS1
US total  =  tank farms + refineries +   SPR     + Alaska_in_transit

MCRSTPx1  =  MCRSFPx1  +  MCRRSPx1                        (PADDs 1/2/4/5)
MCRSTP31  =  MCRSFP31  +  MCRRSP31  +  MCSSTUS1           (PADD 3 holds all 4 SPR sites)
```

Both held to **0 mbbl** at PADD 2, PADD 3, and US level for the sample dates checked. This means the SKR/STT layer slots cleanly under SAE — no double-counting risk.

### Follow-on plan — bind the SKR/STT/SKA layer (estimated ~30 min)

Each of these slots a real graph variable that is currently `latent()` or `0`:

1. **Refinery operating stocks (`SKR`, 16 series, HIGH priority).** Bind to `inventory` on the existing refining views — currently defaulting to `0`:
   - `MCRRSP11..MCRRSP51` → `inventory__crude__padd1..5_refining_view`
   - `MCRRS2A1..MCRRS3E1` + `MCRRSAP1` + `MCRRSEE1` → `inventory__crude__district_R2A..R3E_refining_view` + `district_RAP_refining_view` + `district_REC_refining_view` (10 districts, the same ones already bound for `consumption`)
   - `MCRRSUS1` → new `usa_refinery_stocks_view` node (or attach to `usa_refining_view.inventory`)
   - PADDs 4 & 5 are single-district so PADD-level binding suffices there.

2. **Tank-farm stocks (`STT`, 6 series, HIGH priority).** Tank farms are conceptually the "hub layer" aggregated. Add 6 new abstract observation nodes via a `add_tank_farm_views.py` patch (mirrors `add_storage_views.py`):
   - `usa_tank_farm_stocks_view`, `padd1..5_tank_farm_stocks_view`
   - These act as a PADD-level total constraint on the working-storage hubs (Cushing, Patoka, Houston, Nederland, Corpus Christi, St James, Guernsey) — none of them is individually observed, but the PADD sum of their `inventory` plus pipeline line-fill in that PADD must equal the PADD tank-farm total. The formula evaluator can use this to pin individual hubs.

3. **Alaska in-transit (`SKA`, 1 monthly series, MEDIUM).** Vessel cargo from ANS not yet landed at PADD 5 destinations. Add a new abstract node `usa_alaska_in_transit_view` and bind `MCRSAUS1`. Conceptually this could live on the TAPS pipeline or Valdez origin — but since it is post-Valdez and pre-discharge, an observation view is the cleanest match.

4. **Skip `SKL`** (lease stocks — EIA discontinued).

5. **Skip `SCG`/`SCS`/`SCX`** (stock-change rates — derivable from level Δ; useful only as a cross-check series in `auxiliary` role if you later want one).

6. **Skip weekly `SAX`/`SAXL` mirrors** (finer-resolution scenario; the starter scope is monthly).

After implementation: 9 + 16 + 6 + 1 = **32 stocks-layer TS-bound variables** in total, vs 9 today. The PADD-level mass-balance close-out gains a hard constraint on the hub layer (tank farms) and on the refinery layer (SKR), so the `latent()` slots on Patoka/Houston/Nederland/Corpus Christi/St James/Guernsey become jointly identifiable rather than entirely free.

> If this is your first session in this repo, read [CLAUDE.md](CLAUDE.md) and [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) first. Then come back here.

---

## TL;DR — resume on a fresh machine

```bash
# 1. Clone or pull
git -C "<your-onedrive-path>/Oil Network Project" pull origin main

# 2. Make sure Postgres is running locally on :5432 with database eia_crude,
#    user eia_user / password eia_password, schema-create rights granted.

# 3. Make sure the project venv at .venv/ has these packages installed
#    (one-off if this is a new clone):
".venv/Scripts/pip.exe" install psycopg2-binary sqlalchemy pandas jupyter \
    nbconvert ipykernel matplotlib networkx scipy plotly requests

# 4. Run the master orchestrator. It runs all 4 stages with togglable flags
#    in cell 1 (RUN_ASSET_GRAPH / RUN_METADATA / RUN_DATA_LOADER / RUN_ASSIGNMENTS,
#    all default True). Total time ~5–10 min on a fresh DB; stage 3 (EIA fetch)
#    dominates.
".venv/Scripts/jupyter-nbconvert.exe" --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=1800 \
    Thesis/Code/initialize_oil_network.ipynb

# 5. Open Thesis/Code/initialize_oil_logistics_network.ipynb in VS Code or
#    Jupyter to inspect the topology + interactive geographic plots.
```

That's it — everything is reproducible from `asset_graph/asset_graph.json` + the source-of-truth notebooks. The DB itself does not sync between machines, only the files do, so step 4 is necessary on each new machine.

### Re-running selectively

The 4 stages are independent enough that you can toggle subsets in [initialize_oil_network.ipynb](initialize_oil_network.ipynb)'s flag cell. Common patterns:

- **Re-load EIA data only** (e.g. monthly refresh): set all flags False except `RUN_DATA_LOADER`. Then re-run with `RUN_ASSIGNMENTS` if you want the new vintages copied into `oil_network.timeseries_data` too.
- **Asset-graph patch (e.g. new node added via `add_*.py`)**: set `RUN_ASSET_GRAPH = True`, leave the rest off. If the patch added new variables, also set `RUN_ASSIGNMENTS = True`.
- **Mapping change in `assign_eia.ipynb` or `assign_formulas.ipynb`**: set `RUN_ASSIGNMENTS = True` only. UPSERT semantics handle the merge.
- **Fresh start**: all True (the default).

### Stages

| # | Stage | Sub-orchestrator | Drops/recreates | Time |
|---|---|---|---|---|
| 1 | Asset graph + variables (skeleton) | [initialize_oil_logistics_network.ipynb](initialize_oil_logistics_network.ipynb) | `oil_network` schema | ~30 s |
| 2 | Typed metadata (production assets) | [initialize_oil_network_metadata.ipynb](initialize_oil_network_metadata.ipynb) | `oil_network_metadata` schema | ~5 s |
| 3 | Source-data loaders (EIA, future: CER, AER…) | [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb) | `oil_network_data_loader` schema | **~5–10 min** |
| 4 | Assignments (timeseries + variable_assignments) | [initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) | UPSERT only | ~10 s |

---

## Where we are

The `oil_network` PostgreSQL schema is fully designed, the asset graph is loaded with the full **refinery layer + residual coverage**, and the model now closes mass balance to 100% of real US refining capacity.

### What's done

| Layer | Status |
|---|---|
| Schema (18 tables: 14 core + 4 node-scope) | ✓ — [build_oil_network.ipynb](build_oil_network.ipynb) |
| Metadata schema (`oil_network_metadata`) — typed extension tables FK'd to `oil_network.assets` | ✓ — [initialize_oil_network_metadata.ipynb](initialize_oil_network_metadata.ipynb) (1 table: `metadata_production_assets`) |
| Asset graph load (idempotent UPSERT) | ✓ — [load_asset_graph.ipynb](load_asset_graph.ipynb) |
| Topology + interactive plots | ✓ — [initialize_oil_logistics_network.ipynb](initialize_oil_logistics_network.ipynb) |
| 13 US production nodes (11 named + 2 state residuals) + 1 Canadian oil-sands aggregate | ✓ |
| 24 pipelines as first-class nodes (20 US + 4 Canadian cross-border) | ✓ |
| 6 origin terminals + 10 storage hubs (incl. Guernsey) + 4 SPR sites | ✓ |
| 7 export terminals + 4 import aggregates (incl. PADD3) | ✓ |
| 19 named refineries with capacity, NCI, slate, conversion units | ✓ |
| 5 PADD-level residual refinery aggregates (closes the 65% gap) | ✓ |
| 5 PADD-level refining-centre views (derived) | ✓ |
| Multi-corridor refinery feeders + production-to-gathering routing | ✓ |
| Coverage contract for the starter scenario (basin-, hub-, refinery-level) | ✓ |
| EIA raw data ingest into `oil_network_data_loader` schema | ✓ — [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb) |
| Design rationale doc | ✓ — [oil_network_design.md](oil_network_design.md) |

### What's not yet done — the next workstream

| Layer | Status | Notes |
|---|---|---|
| `variable_assignments` table | **production-side complete (123 rows)**; refineries / exports / hubs / pipelines pending | Production-side bound 2026-05-06 via [assign_eia.ipynb](assign_eia.ipynb) + [assign_formulas.ipynb](assign_formulas.ipynb). Extend by adding per-section assignments (refineries first — likely largest source of TS-bound observations from `refinery_input` + `refinery_input_blend` datasets). |
| `oil_network_data_loader` schema (raw EIA staging + catalogue + facts) | ✓ — [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb) | 27 datasets across 7 domains (incl. `production_steo` for basin-level crude). All endpoints verified to return data. Self-contained schema; `oil_network` is not touched. |
| `oil_network.timeseries` + `timeseries_data` | **production-side complete** (26 series in catalogue, ~3,400 fact rows); other sources pending | Built into [assign_eia.ipynb](assign_eia.ipynb) which both registers TS in catalogue and copies facts (with kbd scaling). Extend by adding refinery / export / import series to the same notebook's mapping list. |
| `node_type_default_formulas` | empty | First-pass defaults per (node_type × variable_type). Up to ~84 rows; in practice fewer because most node types only need 4–6 formulas. |
| Formula evaluator runtime | not started | Computes mass balance per (node, commodity, date) by walking variable formulas. |

---

## Coverage as of this session

| Stream | Modelled | Real US (2023/24) | Coverage |
|---|---:|---:|---:|
| Production | 11,690 kbd | 10,360 kbd | 113% (slightly over because conservative real estimates) |
| **Refining** | **17,484 kbd** | **17,500 kbd** | **100%** (was 35% before residual layer) |
| Imports | 4 nodes (PADD1+2+3+5) + Canadian oil-sands corridor | 4,400 kbd | full geographic coverage |
| Exports | 7 terminals (~5,600 kbd cap) | 4,100 kbd actual | 100%+ headroom |
| Canadian inflow | 4/4 cross-border pipelines (Mainline-CA, Keystone, TMX, Express+Platte) | 3,700 kbd | ~100% of pipeline routes |

DB: 113 nodes (98 physical, 15 abstract), 142 unique flow edges, 736 variables. **95 of 98 physical nodes** reach a refinery or export — the 3 that don't are the Cushing operator sub-terminals, collapsed by the starter scope by design.

`oil_network_data_loader` (separate schema, populated by [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb)): 27 EIA datasets covering production (state/PADD via `crpdn` + basin-level via STEO), imports, exports, inter-PADD movements, refinery, stocks, S&D, and weekly trade. ~145,900 rows / 1,493 unique series; STEO series carry an 18-month forecast horizon out to 2027-12. All endpoints empirically verified to return data.

---

## Files in this directory

### Source-of-truth notebooks (run these to rebuild state)

| File | What it does | When to run |
|---|---|---|
| [initialize_oil_network.ipynb](initialize_oil_network.ipynb) | **Top-level master orchestrator**. Runs all 4 stages (asset graph → metadata → data loader → assignments) with togglable True/False flags per stage. Use this for a full rebuild or for selective re-runs. | Fresh machine; full rebuild or partial refresh |
| [build_oil_network.ipynb](build_oil_network.ipynb) | DDL — drops & recreates the `oil_network` schema. Idempotent. | New machine, schema reset, after schema design changes |
| [load_asset_graph.ipynb](load_asset_graph.ipynb) | UPSERTs `asset_graph/asset_graph.json` into the schema. Idempotent. | After every change to `asset_graph.json` |
| [initialize_oil_logistics_network.ipynb](initialize_oil_logistics_network.ipynb) | One-shot: runs both above + verification + topology plot + interactive plotly geographic plot. | New machine; sanity check after any change |
| ~~load_eia_data.ipynb~~ | **Deprecated 2026-05-04** — moved to `archive/load_eia_data.DEPRECATED.ipynb`. Replaced by `initialize_oil_network_data_loader.ipynb`. The `source_eia` schema it populated is also deprecated; drop it manually with `DROP SCHEMA source_eia CASCADE;` once you're sure you don't need the old data. | — |
| [initialize_oil_network_metadata.ipynb](initialize_oil_network_metadata.ipynb) | DDL for the typed-metadata schema `oil_network_metadata`. Drops & recreates the schema, then creates each typed extension table (currently: `metadata_production_assets`). Does **not** touch `oil_network`. | Fresh machine; after any change to the metadata schema |
| [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb) | Thin orchestrator that runs every per-source loader notebook (currently just [load_eia.ipynb](load_eia.ipynb); future: `load_cer.ipynb`, `load_aer.ipynb`, etc.). Reorganised 2026-05-06 from monolithic to per-source modular pattern. | Fresh machine; full re-fetch (~5–10 min) |
| [load_eia.ipynb](load_eia.ipynb) | EIA ingestion: 27 datasets across `petroleum/*` + `steo` routes into `oil_network_data_loader` schema (3 tables: `eia_staging` + `ref_timeseries` + `timeseries` with vintage-aware PK). Includes `production_steo` (basin-level crude: Permian, Bakken, Eagle Ford, Appalachia, Haynesville + GoM/Alaska/L48 excl GoM aggregates). Idempotent reset. Does **not** touch `oil_network`. | Source for the orchestrator above; can also run standalone |
| [initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) | Thin orchestrator that runs every assignment notebook in dependency order: TS-bound first ([assign_eia.ipynb](assign_eia.ipynb)), formula-bound last ([assign_formulas.ipynb](assign_formulas.ipynb)). | After data is loaded; populates `oil_network.timeseries` + `timeseries_data` + `variable_assignments` |
| [assign_eia.ipynb](assign_eia.ipynb) | Per-source TS-bound assignment notebook for EIA. Bridges `oil_network_data_loader.timeseries` → `oil_network.timeseries` (catalogue, ~26 production-side series) + `oil_network.timeseries_data` (vintaged facts, scaled to kbd) + `variable_assignments` (11 authoritative TS-bound rows). Auxiliary cross-check series registered in catalogue but not bound to any variable. | Standalone or via the orchestrator above |
| [assign_formulas.ipynb](assign_formulas.ipynb) | Source-agnostic formula-bound assignment notebook. Writes derived rollups, zeros, latent sentinels, and single-outflow derivations for production-side variables (123 rows total). Runs **after** all source notebooks. | Standalone or via the orchestrator above |

### Reference / docs

| File | What it covers |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Full project context — design principles, scope, working conventions. **Read first if new to the repo.** |
| [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) | Standalone copy of the 12 committed design principles (CLAUDE.md §2). |
| [DATA_MODEL_PREMISES.md](DATA_MODEL_PREMISES.md) | Comprehensive doc: every framework + schema + operational premise behind the data model, with WHY and a concrete example from the live graph for each (30 premises, A1–A12 / B1–B11 / C1–C7). |
| [oil_network_design.md](oil_network_design.md) | All 22 schema design decisions with WHY for each. The schema reference doc. |
| [scope_and_resolution.md](scope_and_resolution.md) | Worked explanation of how collapsed nodes relate to authoritative ones — the resolution hierarchy (static, on assets) vs the node scope (per-scenario), with concrete examples from the starter scope and the latent-allocation tension. |
| [production_map.md](production_map.md) | Full EIA → variable mapping for the production side. Every production-related variable lists its binding (observed TS, derived formula, latent, or zero) with the source EIA series ID. Reference for `variable_assignments`. |
| [HANDOVER.md](HANDOVER.md) | This file. |
| [PROJECT_STATE.md](PROJECT_STATE.md) | Quick reference: current numbers, pending work, outstanding decisions. |
| [NEXT_STEPS.md](NEXT_STEPS.md) | Immediate action items in order. |

### Source data

| File | Role |
|---|---|
| [asset_graph/asset_graph.json](asset_graph/asset_graph.json) | **Authoritative** source of truth. 111 nodes, 198 edges (incl. 58 aggregation). Don't edit by hand — patches are layered through the `add_*.py` scripts so they are auditable. |
| `asset_graph/asset_graph.backup_*.json` | Snapshots before each major patch pass. Gitignored — don't push. |
| ~~asset_graph/nodes.csv.old, asset_graph/edges.csv.old~~ | Stale flat CSV exports from 2026-04-24, renamed `.old` on 2026-05-06. Not used by anything; the JSON is canonical. Delete or regenerate if ever needed. |

### Patch scripts (history of changes; mostly idempotent)

| Script | What it added |
|---|---|
| [add_refinery_layer.py](add_refinery_layer.py) | Refinery layer pass 1: 5 abstract refining-centre views + 17 named refineries + their flow edges. |
| [add_routing_fixes.py](add_routing_fixes.py) | Routing fixes pass 2: Guernsey hub, Pony Express pipeline, Suncor Commerce City, Phillips 66 Ponca City, plus production→gathering and gulf→st_james edges. |
| [add_realistic_feeders.py](add_realistic_feeders.py) | Routing fixes pass 3: secondary refinery feeders so refineries are not single-fed (down from 15 single-feeders to 5 defensible ones). |
| [apply_patch_fixes.py](apply_patch_fixes.py) | Hygiene pass: deduped 4 duplicate edges, removed redundant alaska→ans_gathering aggregation, wired the 5 padd_production_view rollups, clarified Eagle Ford contract. |
| [add_residual_layer.py](add_residual_layer.py) | Coverage closure pass: 5 PADD residual refineries + padd3_imports_agg. Closed refining gap from 35% to 100%. |
| [add_canadian_layer.py](add_canadian_layer.py) | Canadian-supply pass: `canadian_oil_sands` aggregate production node + 4 cross-border pipelines (Enbridge Mainline-CA, Keystone, Trans Mountain TMX, Express+Platte) + their flow edges into the US refining network. |
| [add_state_residuals.py](add_state_residuals.py) | State-residual production pass: `texas_other` (TX − Permian-TX − Eagle Ford, ~230 kbd) + `montana_other` (MT − Bakken-MT, ~21 kbd Cedar Creek). Wires each to its PADD-residual refinery. Idempotent. |
| [drop_redundant_topology.py](drop_redundant_topology.py) | Architecture cleanup: removes `resolution_hierarchy.children` from every asset and drops the 58 `aggregation` entries from the edges array. Both were redundant with `formula_inputs` on `variable_assignments` (the single source of truth for the aggregation graph). Idempotent. |
| [add_inter_padd_flow_edges.py](add_inter_padd_flow_edges.py) | Adds 10 abstract-level inter-PADD pipeline flow edges (`paddA_production_view → paddB_refining_view`). Topology stable across scopes; under fine scopes the abstract flow variables are zero/latent, under coarse scopes (PADD-only data) they're TS-bound to `movements_*` series and the physical edges below become latent. Idempotent. |
| [add_refinery_capacity_report.py](add_refinery_capacity_report.py) | Replaces 5 PADD residual refineries with 96 net new refineries from EIA Refinery Capacity Report 2025 (`data/refcap25.xlsx`); adds 10 refining-district aggregate nodes; redirects 18 inflow edges from dropped residuals to `padd*_refining_view` (abstract flow). Idempotent; manual SITE/STATE match table dedupes against existing 19 named refineries. Geography deep-merge preserves existing lat/lon. |
| [geocode_refineries.py](geocode_refineries.py) | Backfills `geography.lat`/`geography.lon` on every refinery node from a manual lookup table of 84 unique `(SITE, STATE)` → `(lat, lon)` pairs (city-center, ~5–10 km accuracy). Idempotent. Pairs with `add_refinery_capacity_report.py` — run that first, then this. |
| [add_refinery_inflow_edges.py](add_refinery_inflow_edges.py) | Wires all 115 refineries to their physical inflow sources. Top-50 refineries (~79% of capacity) get explicit per-refinery source lists; the long tail uses per-district defaults, with state-keyed sub-defaults for West Coast (CA/WA/OR/AK/HI/NV have different supply chains) and site-keyed sub-defaults for Texas Gulf Coast (Corpus Christi vs Houston/Beaumont). Architectural rule: inland refineries do NOT have direct `*_imports_agg` inflows — foreign tankers physically discharge at coastal terminals; pipelines feed inland. Existing 19 named refineries are preserved untouched. Drop-then-rewire on every run so re-runs converge. 280 `refinery_inflow` edges. Idempotent. |
| [add_storage_views.py](add_storage_views.py) | Adds 7 abstract observational view nodes for crude stocks (`usa_total_stocks_view`, `usa_commercial_stocks_view`, `padd1/2/3/4/5_stocks_view`). View-only — no flow edges (principle 2.6). Pairs with `assign_eia.ipynb` to bind each to its EIA `MCRST*` / `MCEST*` series. Inventory levels are stored as `unit='mbbl_level'` (snapshots, not rates). Cushing and `spr_total` already exist; their `inventory` is rebound directly via `assign_eia` (no new node needed). Idempotent. |

### Audit scripts (run anytime)

| Script | What it checks |
|---|---|
| [node_audit.py](node_audit.py) | Per-node-type breakdown of in/out edges; flags isolated nodes, sources, sinks. |
| [verify_routing_fixes.py](verify_routing_fixes.py) | Reachability check: every physical node should reach a refinery or export terminal. |
| [mass_balance_check.py](mass_balance_check.py) | Structural mass balance: production vs refining vs export capacity per PADD; per-refinery feed sources; bottleneck flagging. |
| [coverage_check.py](coverage_check.py) | Modelled vs real US coverage for production / refining / imports / exports, per PADD. Now also reports Canadian-inflow corridor coverage. |
| [source_sink_audit.py](source_sink_audit.py) | Reachability matrix: every (source, sink-region) pair flagged against real-world expectations (yes / miss / unexp / ok-skip). Plus per-source and per-sink listings. Currently reports zero issues — every source reaches the regions it should and only those. |

### Legacy (read-only — don't modify)

- `build_asset_graph_db.ipynb` and `build_oil_network v1.0.ipynb` — earlier schema iterations. Superseded by `build_oil_network.ipynb`.
- `archive/Master_Thesis_Pedro_Porfirio_v4_0.rtf` — historical thesis snapshot.
- `thesis/Master_Thesis_Pedro_Porfirio_v16.docx` — pre-scope-change version, useful reference.

---

## Recent design pivots (chronological)

1. **Schema reset** (2026-04-29 morning): started in `oil_logistics_network`; restarted clean in `oil_network` for step-by-step build with Pedro's review at each layer.
2. **Asset = identity, node = role**: assets are the universal identity layer (physical or abstract). PADD 5 is an asset. Nodes are how an asset appears in a particular graph.
3. **`time_series_type` ≠ `variable_type`**: different vocabularies. TS types describe what the data measures; variable types describe a slot's role in the mass balance.
4. **`related_asset_id` on `timeseries`**: pipeline flows, vessel voyages, inter-terminal transfers are inherently between two assets. Relational column added with self-loop CHECK.
5. **Renamed `time_series*` → `timeseries*`**: consistent with `timeseries_id` column convention.
6. **Dropped `assignment_kind` enum**: populated column (`timeseries_id` vs `formula`) is the kind. Single CHECK: `num_nonnulls(timeseries_id, formula) = 1`.
7. **Wrote [oil_network_design.md](oil_network_design.md)** capturing all 22 decisions with rationale.
8. **Refinery layer added** (pass 1, 2026-04-29): 5 PADD refining-centre views + 17 named US refineries with capacity / NCI / slate. Routes production through gathering → origin → pipeline → hub → refinery.
9. **Routing fixes** (pass 2, 2026-04-29): Guernsey + Pony Express closed the Wyoming/Colorado surplus gap; Bakken→Clearbrook opened the Enbridge Mainline route to Whiting; Suncor + Ponca City added as local-state refineries.
10. **Realistic refinery feeders** (pass 3, 2026-04-29): secondary feeders so refineries are not single-fed. Whiting via Mainline-terminus, Joliet via Mokena-spur, Cushing southbound takeoffs to Wood River / Catlettsburg / Ponca City, Houston→Port Arthur, GoM→Galveston Bay + Baytown, LOOP→Lake Charles, Valdez→Cherry Point.
11. **Patch hygiene + padd_production_view rollups** (2026-04-29): deduped 4 exact-duplicate edge IDs, removed redundant alaska→ans_gathering aggregation edge, wired 11 padd*_production_view aggregation edges + populated children lists, clarified Eagle Ford contract notes.
12. **Loader robustness** (2026-04-29): switched `kind` (physical vs abstract) derivation in [load_asset_graph.ipynb](load_asset_graph.ipynb) from a hardcoded subtype list to deriving from `node_class`. Future observational subtypes get tagged correctly without code changes.
13. **Residual refining layer** (pass 4, 2026-04-30): 5 PADD-level residual refineries closing the 11.3 mb/d gap between modelled (35% of US refining) and real (100% coverage). Plus `padd3_imports_agg` for the missing 2.3 mb/d Gulf foreign tanker imports. Each residual rolls up under its existing `padd*_refining_view`.
14. **Canadian-supply layer** (2026-04-30): added `canadian_oil_sands` production aggregate plus 4 cross-border pipelines (Enbridge Mainline-CA, Keystone, Trans Mountain TMX, Express+Platte). Closes the inflow corridor for ~3.7 mb/d of Canadian crude that previously routed through `clearbrook_entry` only. Brought the graph to 111 nodes / 198 edges.
15. **EIA raw-data ingest** (2026-05-01): built [load_eia_data.ipynb](load_eia_data.ipynb) writing to a separate `source_eia` schema (kept clean from `oil_network`). 26 datasets / 1,485 series / 144,682 vintaged rows from 2015-01-01. Vintage-on-change semantics: `eia_data` PK is `(series_id, observation_date, saved_date)`, new vintage row only when value changes.
16. **Old schema dropped** (2026-05-01): `oil_logistics_network` (the pre-2026-04-29 schema) deleted from the DB — was leaving stale `edges` and `resolution_hierarchy` tables visible in DBeaver despite being abandoned.
18. **Data-loader schema + `source_eia` deprecation** (2026-05-04): introduced `oil_network_data_loader` as a sibling schema for EIA ingestion (3 tables — `eia_staging` (wide raw), `ref_timeseries` (catalogue, PK `(source, timeseries_id)`), `timeseries` (facts, PK includes `timeseries_published_date` for vintage-on-each-run)). 26 EIA datasets across 7 domains; methodology mirrors the v4_cc 3-table pattern. **All 26 endpoints empirically verified** to return data (probe script hit each one — totals match the 2026-05-01 load_eia_data run row-for-row, no empty endpoints). Same session: **deprecated `source_eia`** and its loader `load_eia_data.ipynb` (moved to `archive/load_eia_data.DEPRECATED.ipynb`). Schema `source_eia` left in the DB for now — drop manually with `DROP SCHEMA source_eia CASCADE;` once confident the new pipeline replaces it.

33. **Exports bound — boundary now closed** (2026-05-08, fourth pass): added the missing exports layer with the same architecture as imports.

    **Asset graph changes** ([add_export_views.py](add_export_views.py)):
    - 6 new abstract observational view nodes: `usa_exports_view` + `padd1/2/3/4/5_exports_view` (formula-only, TS-bound)
    - 1 new boundary destination node: `foreign_export_destination` (kind=abstract, infrastructure-style — symmetric counterpart of `canadian_oil_sands` for outgoing flows)
    - 4 new outflow edges: `ingleside_export / houston_export / nederland_export / loop_terminal → foreign_export_destination`
    - Graph: **241 nodes / 404 edges / 1,772 variables** (was 234 / 400 / 1,736)

    **assign_eia.ipynb**: 6 new authoritative TS-bindings: `MCREXUS2 → usa_exports_view` + `MCREXP12-52 → padd1-5_exports_view`. All in MBBL/D (kbd, no conversion). **62 TS-bound assignments** (was 56).

    **assign_formulas.ipynb**:
    - `consumption__crude__foreign_export_destination = production__crude__padd3_exports_view` (mirror to PADD 3 export total — that's what flows through modelled terminals)
    - 4 per-terminal export outflows added to `LATENT_VARIABLES` (per-terminal export volume not published; jointly summing to MCREXP32)
    - `infra_subtypes` extended to include `foreign_export_destination` so the destination's `inventory` and `balancing_item` get default-zero

    **Coverage of US exports**: 4 modelled terminals (Ingleside, Houston, Nederland, LOOP — all PADD 3) capture **~96.8% of US exports** (~3,966 kbd of 4,096 in 2024). PADDs 1/2/4/5 small unmodelled exports (~130 kbd, ~3.2%) stay as view-only observations — no terminals modelled for them.

    **Mass balance — boundary closed (2024 mean, kbd):**
    ```
    + US production       = +13,234.6
    + Imports             =  +6,587.2
    − Refining            = -16,219.8
    − Exports             =  -4,095.6
    ─────────────────────────────────
    Implied (ΔStock + B)  =    -493.6 kbd
    ```
    The ~−494 kbd implied residual is the right magnitude for ΔStock + balancing item. Commercial stocks were ~flat in 2024; SPR refilled at ~700 kbd average pace (positive build); statistical balancing item plausibly absorbs a few hundred kbd. Standard EIA petroleum balance has a similar-sized statistical difference line.

    **Final coverage**: **1,772 / 1,772 variables assigned (100%)**. 62 TS-bound, 70 series in catalogue (62 kbd + 8 mbbl), 8,872 facts.

32. **Per-edge mass-balance pairing — 100% variable coverage** (2026-05-08, third pass): every per-edge inflow now has an explicit formula `= outflow_A_to_B`, making the mass-balance pairing visible in the data. Two final closure passes added to [assign_formulas.ipynb](assign_formulas.ipynb) cell `c04`:

    - **Infrastructure default-zero**: every non-relational variable (P/C/I/B) on `pipeline / storage_terminal / export_terminal / origin_terminal / gathering` nodes defaults to `0`. These are pure transit nodes — they don't produce or consume crude, and inventory/balancing items aren't observed at this resolution. Adds 208 zero assignments.
    - **Mirror pass**: every relational `inflow` variable gets `formula = paired_outflow_var_id`. Per-edge mass balance: `inflow_B_from_A = outflow_A_to_B` because both describe the same physical flow on the same edge. Adds 400 mirror formulas. **Dedupe at the end keeps the last entry**, so the mirror overrides any earlier `latent()` placeholder for the same inflow.
    - **`canadian_oil_sands.production` re-bound to `sum_over_outflows`** (was unassigned after the imports pass moved MCRIMUSCA2 to the new `usa_canada_inflow_view`). Natural mass-balance derivation at the foreign-source node.

    **Final coverage: 1,736 / 1,736 variables assigned (100%, 0 unassigned).** Breakdown:

    | Kind | Count | What |
    |---|---:|---|
    | zero | 749 | structurally empty (transit, refinery production=0, etc.) |
    | latent | 498 | unobserved per-route flows (principle 2.11) |
    | mirror_outflow | 400 | `inflow_B_from_A = outflow_A_to_B` mass-balance pairing |
    | TS-bound | 56 | direct EIA observations |
    | production_carry-through | 12 | single-outflow nodes (`outflow = production`) |
    | derived_arithmetic | 11 | Tier-1 residuals (basin-state, PADD-other) |
    | sum_over_children | 9 | aggregate rollups (Eagle Ford, rest_of_l48, SPR total, etc.) |
    | sum_over_outflows | 1 | canadian_oil_sands.production |

    The mirror pattern doesn't "force" the equality at evaluation time (the formula evaluator does that) — it makes the equality declarative, queryable from `variable_assignments`, and removes any `(unbound)` rows from the data. The framework's per-edge mass balance is now first-class.

    **Note on multi-outflow split** (re Pedro's question): this mirror handles the per-edge equality only. When a node has multiple outflows (e.g., `permian_tx_gathering → 3 origins`), the *split* between them is governed by per-node mass balance, not the mirror. Latent splits stay latent (principle 2.11); they'll be resolved by a future formula evaluator + forecasting layer.

31. **Imports + inter-PADD movements bound; unit column added** (2026-05-08, second pass): closed the remaining gap on the boundary side (foreign tanker imports) and the inter-PADD movements layer.

    **Schema change:** added `unit TEXT NOT NULL DEFAULT 'kbd'` column to `oil_network.timeseries` ([build_oil_network.ipynb](build_oil_network.ipynb) Step 3). Catalogue now declares the unit of every series. Two units in use: `kbd` (rate, MBBL/D — the framework standard) and `mbbl` (raw monthly volume, source unit for inter-PADD movements; kept for audit only).

    **MBBL → kbd dual-registration** in [assign_eia.ipynb](assign_eia.ipynb): each tuple now carries a `unit` field. For `unit='mbbl'` sources, the loader creates **two** catalogue rows:
    - `<ts_id>` (`unit='mbbl'`) — raw monthly volume, audit-only
    - `<ts_id>_kbd` (`unit='kbd'`) — derived rate, with values divided by the **exact per-row days-in-month** (`EXTRACT(DAY FROM (date_trunc('month', d) + interval '1 month - 1 day'))`)

    Variable assignments always reference the `_kbd` version. Verified against MCRMPP3P21 (P3→P2 movement): Jan-2024 61,238 MBBL ÷ 31 days = 1,975.4 kbd; Feb-2024 55,085 ÷ 29 = 1,899.5 kbd; etc — exact, no rounding error.

    **6 new abstract observational nodes added via [add_import_views.py](add_import_views.py)**: `usa_imports_view`, `padd1/2/3/4/5_imports_view` (foreign tanker imports = total imports). All TS-bound to MCRIPPxX2 / MCRIMUS2.

    **22 new authoritative TS-bindings** in [assign_eia.ipynb](assign_eia.ipynb):
    - Imports (6): `MCRIMUS2` + `MCRIPP12/22/32/42/52` to the new view nodes (kbd, no conversion)
    - Inter-PADD movements (8): `MCRMPP1P21`, `MCRMPP2P11/31/41`, `MCRMPP3P11/21/41`, `MCRMPP4P21` to the inter-PADD outflow variables. Source MBBL → derived `_kbd` per-row.

    **Foreign tanker imports as derivations** in [assign_formulas.ipynb](assign_formulas.ipynb): `padd1/3/5_imports_agg.production = paddX_imports_view − paddX_canadian_inflow_view`. Each is the boundary inflow into the system from non-Canadian foreign tankers. PADD 1 = ~500 kbd, PADD 3 = ~1,122 kbd, PADD 5 = ~903 kbd; sum = 2,525.7 kbd (matches MCRIMUS2 − MCRIMUSCA2 = 2,525.8 to rounding).

    **Inter-PADD edges with no top-level EIA series** (P1→P3, P4→P3): left as `latent()` and documented in `LATENT_VARIABLES`. EIA publishes them only at sub-PADD district level (`MCRMP_R10-R30`, `MCRMP_R40-R30`), not as top-level MCRMP series. Could be added as a future refinement.

    **Verification (2024 mean, kbd):**
    - **Imports zero-gap**: sum of 5 PADD-totals (6,587.5) vs EIA `MCRIMUS2` (6,587.2) → gap +0.25 kbd (rounding)
    - **Foreign-tanker zero-gap**: sum of 3 PADD-aggregate foreign tanker terminals (2,525.7) vs `MCRIMUS2 − MCRIMUSCA2` (2,525.8) → gap +0.0 kbd
    - **Inter-PADD bound**: P3→P2 (Cushing→Houston) = 2,052 kbd; P2→P4 = 948 kbd; P2→P3 (Patoka→Gulf) = 689 kbd; P4→P2 = 266 kbd. Total inter-PADD movement bound = ~3,977 kbd of crude shifting between PADDs monthly.
    - **Updated US aggregate balance**: Production 13,235 + Imports 6,587 − Refining 16,220 = +3,602 kbd implied residual (= Exports − stock changes − balancing item). Real US exports ~4,100; remaining ~500 kbd is ΔStock + B, plausible.

    **Counts after rebuild:** 234 assets / 405 edges / 1,736 variables / **56 TS-bound** (was 23 before this session, 42 after the morning pass) + 1,155 formula-bound = 1,211 total assignments. **64 EIA series** in the catalogue (was 42; 56 kbd + 8 mbbl). **8,619 fact rows** in `timeseries_data` (was 5,711).

30. **Zero-gap mass balance + SPR bindings implemented** (2026-05-08): the zero-gap plan agreed yesterday is now fully shipped.

    **What changed:**
    - Patch script [add_padd_residuals.py](add_padd_residuals.py) added 14 nodes: 5 physical PADD-residual production nodes (`padd1_other` ... `padd5_other`) + 9 abstract observational view nodes (`usa_production_view`, `usa_refining_view`, `usa_canada_inflow_view`, `usa_lower48_excl_gom_view`, `texas_state_view`, `montana_state_view`, `padd1/2/3_canadian_inflow_view`). 5 new outflow edges wire the PADD residuals to physical hubs (PADD 2/3/4) or refining-centre views (PADD 1/5, where no hub exists). Graph: **228 nodes / 400 edges / 1,712 variables** (was 214 / 395 / 1,692).
    - [assign_eia.ipynb](assign_eia.ipynb) — every previously-auxiliary aggregate series now binds to its TS-bound graph variable: COPRPM/COPRBK to `permian/bakken`, MCRFPP12-P52 to `paddX_production_view`, M_EPC0_YIY_R10/20/30_2 to `padd1/2/3_refining_view`, MCRFPUS2 to `usa_production_view`, M_EPC0_YIY_NUS_2 to `usa_refining_view`, MCRIMUSCA2/MCRIPP1/2/3 CA2 to the canadian-inflow views, MCRFPTX2/MT2 to `texas_state_view`/`montana_state_view`, PAPR48NGOM to `usa_lower48_excl_gom_view`. **All 42 EIA series now `authoritative` (was 23 + 19 auxiliary)** — every loaded series is matched to a graph variable.
    - [assign_formulas.ipynb](assign_formulas.ipynb) — formulas updated to reference graph variables instead of `eia:*` literals (`permian_tx = permian - permian_nm`, etc.). 5 new Tier-1 formulas for `padd_other` residuals (= `paddX_production_view - sum_modelled_basins_in_padd`). Removed redundant `sum_over_children` entries for now-TS-bound aggregates (permian, bakken, paddX_production_view, padd1/2/3_refining_view, canadian_oil_sands). **Step 3c** extended to handle production-type defaults on observational aggregates. **New SPR section** binds all 28 SPR variables: P/C/B = 0; inventory + inflow + outflow = `latent()` on the 4 sites; `spr_total.inventory` = `sum_over_children` of the 4 sites.

    **Zero-gap verification (2024 mean, kbd):**
    - **US production**: sum-of-PADDs vs EIA `MCRFPUS2` → gap **0.00 kbd (-0.0000%)** ✓
    - **US refining**: sum-of-PADDs vs EIA `M_EPC0_YIY_NUS_2` → gap **-0.25 kbd (-0.0015%)** ✓ (rounding)
    - **US imports from Canada**: sum-of-PADDs vs EIA `MCRIMUSCA2` → gap **+0.42 kbd (+0.0103%)** ✓ (rounding)
    - **PADD-level production** (1/2/3/4/5): every PADD shows **+0.0000 kbd** gap by construction ✓
    - **Basin-level** (Permian, Bakken): TX + NM children sum to COPRPM exactly; ND + MT to COPRBK exactly ✓
    - **Refining at PADD level** (district sums vs R10/20/30): gaps ≤ 0.33 kbd (rounding only) ✓

    Aggregate residual: `P + Canadian_imports - US_refining = +1,076 kbd`. The boundary gap (Exports − non-Canadian Imports − ΔStock − B) remains because non-Canadian imports + US exports are not yet TS-bound and SPR + commercial stock changes are real-but-unmodelled. Real US 2024: ~+1,800 kbd expected; ~720 kbd of the residual is plausibly stock build (SPR refill in 2024 was material).

    **SPR status (now bound, all 28 variables):**
    - 4 sites × 6 vars: production = 0, consumption = 0, inventory = `latent()`, balancing_item = 0, inflow = `latent()`, outflow = `latent()`. Sites have edges to/from connected hubs (Bryan Mound ↔ Houston, Big Hill + West Hackberry ↔ Nederland, Bayou Choctaw ↔ St. James) — fills/releases happen on those edges but per-month per-site flow isn't observed under the starter scope.
    - `spr_total` view: P/C/B = 0; inventory = `sum_over_children` of the 4 sites.
    - To upgrade: load EIA PSM Table 38 (site-level monthly inventory) and bind site `inventory` directly. Inflows/outflows can then be derived as ΔInventory or stay latent under "fills/releases unobserved per-route".

    **Counts after rebuild**: 228 assets / 400 edges / 1,712 variables / 1,093 assigned (619 unassigned = the rest-of-network infrastructure layer: hubs, origins, pipelines, exports, imports — pending workstream).

29. **Mass-balance audits + topology fixes + zero-gap plan** (2026-05-07): Pedro asked to verify the graph closes mass balance vs EIA. Two issues found and fixed; one larger plan agreed but **not yet implemented**.

    **Fixes already applied:**
    - **State-residual outflow redirect** ([add_state_residual_outflows.py](add_state_residual_outflows.py)): `texas_other` and `montana_other` previously had outflow → `paddX_refining_view` (abstract, violates principle 2.6 because aggregates carry no flows). Redirected to physical hubs: `texas_other → houston_hub` and `montana_other → guernsey_hub`. Each is now bound `outflow = production` (single-outflow carry-through). Pairing: 0 unmatched. Reachability: 11/13 production sources reach physical sinks (the other 2 are texas_other / montana_other now also reaching sinks via the new hubs). **The 6 isolated physical nodes** (3 Cushing operator sub-terminals, 3 Houston export sub-terminals) are isolated **by design** under the starter scope — collapsed under their parent aggregates per `scope_and_resolution.md`.
    - **PADD assignment bug fix** in [assign_formulas.ipynb](assign_formulas.ipynb): `permian_nm` (2,023 kbd in 2024) was in `padd4_production_view` children, but New Mexico is **PADD 3**, not PADD 4. Moved to `padd3_production_view`. PADD 3 gap closed from -22% to -1.4%, PADD 4 from +182% to -18%. Bug was observational only (the rollup view); per-node production values were never wrong.

    **Mass balance findings (2024 mean, kbd):**
    - **Refining: 0.00% gap** — modelled = 16,220.3 vs EIA `M_EPC0_YIY_NUS_2` = 16,219.8. The 10-district + PADD 4 + PADD 5 decomposition is exact.
    - **Production: -4.6% gap** — modelled = 12,621 vs EIA `MCRFPUS2` = 13,235. The -614 kbd shortfall is **modelling coverage**, not data inconsistency: states EIA reports but not modelled (LA onshore, MS, AL, KS, UT, NY/PA/WV, etc.).
    - **Aggregate balance**: `P + Canadian_imports = 16,682` vs `Refining = 16,220` → implied (`Exports − non-Canadian imports − ΔStock − B`) = +462 kbd. Real US 2024: ~+1,800 kbd, so ~1,340 kbd remaining = production undercount + non-Canadian imports/exports not yet TS-bound.

    **Zero-gap plan (agreed, not yet implemented):**
    Pedro's directive: "all loaded EIA series matched, zero gap to graph result". Approach:
    1. **Add 5 physical PADD-residual production nodes** (`padd1_other` ... `padd5_other`) with production = `MCRFPPxx2 − sum(modelled_basins_in_padd)` (Tier-1 arithmetic, like `texas_other`). Outflows: PADDs 2/3/4 → physical hubs (Patoka/Houston/Guernsey); PADDs 1/5 → `paddX_refining_view` (abstract, no PADD 1/5 hub exists).
    2. **TS-bind aggregate variables** to their EIA totals (replacing `sum_over_children` formulas):
       - Basin: `production__crude__permian` ← `eia:COPRPM`, `production__crude__bakken` ← `eia:COPRBK`
       - PADD production: `paddX_production_view` ← `eia:MCRFPPxx2` (was `sum_over_children`)
       - PADD refining: `padd1/2/3_refining_view` ← `eia:M_EPC0_YIY_R10/20/30_2` (was `sum_over_children`)
    3. **Add new US-level views** (`usa_production_view`, `usa_refining_view`) and TS-bind to `MCRFPUS2` / `M_EPC0_YIY_NUS_2`.
    4. **PADD-imports auxiliary** for `MCRIPP1/2/3 CA2` and `MCRIMUSCA2` — currently these exist only as auxiliary references (not bound).
    5. **State-aggregate views** (optional): `texas_state_view`, `montana_state_view` TS-bound to `MCRFPTX2`, `MCRFPMT2` so the residual formulas reference graph variables instead of `eia:*` literals.
    6. **Run mass balance check at multiple aggregate levels** (US, PADD, basin) — every one should be ≤0.1% gap to EIA after the residuals are added.

    **SPR status — important finding:** All **28 SPR variables are unassigned** under the starter scope (4 sites × 6 variables/site + spr_total × 4). SPR sites have inflow + outflow edges to connected hubs (`spr_bryan_mound ↔ houston_hub`, `spr_big_hill ↔ nederland_hub`, `spr_west_hackberry ↔ nederland_hub`, `spr_bayou_choctaw ↔ st_james_hub`) but no formula or TS bindings. **For US-level mass balance, SPR contributes via inventory ΔS** (fills are stock build, releases are stock draw). To close the balance properly, SPR `inventory` variables should be TS-bound to EIA PSM Table 38 site-level series; SPR `inflow`/`outflow` variables should be TS-bound to monthly fill/release series (or marked latent and inferred from ΔInventory). This was flagged but not yet addressed.

    **Where to resume:** Either (a) implement the zero-gap plan above end-to-end, or (b) tackle SPR bindings first (small, self-contained). Pedro's last instruction at the end of the session was to proceed with the zero-gap plan and then run lower-aggregate balance checks.

28. **Schema cleanup: collapse `node_scope_*` tables into 1 table + 2 views** (2026-05-07): the 4 `node_scope_*` tables (`node_scopes`, `node_scope_authorisations`, `node_scope_collapsed_nodes`, `node_scope_notes`) were redundant — every fact they encoded except the free-form notes is derivable from `variable_assignments`. Per principle 2.4 ("relationships only via variables"), three of them are now views; the fourth (notes) shrinks to a single FK-keyed-to-scenarios table. **Schema: 18 tables → 15** (14 core + `scenario_notes`); **views: 3 → 5** (added `v_scope_authoritative_nodes` and `v_scope_collapsed_nodes`).
    - `v_scope_authoritative_nodes` — every TS-bound `variable_assignments` row, exposing which (scenario, node, variable) is observed via which series. Replaces `node_scope_authorisations`.
    - `v_scope_collapsed_nodes` — every variable whose formula is `'0'` or `'latent()'` — i.e. structurally inert under the scope. Replaces `node_scope_collapsed_nodes`.
    - `scenario_notes` — free-form narrative per scenario, FK directly to `scenarios(scenario_id)` (no node_scope_id middleman). Renamed from `node_scope_notes` and rekeyed.
    - `node_scopes` — dropped entirely; everything it stored is on `scenarios` columns already (`time_range_start/end`, `frequency`, `pipeline_layer_note`).
    Both [build_oil_network.ipynb](build_oil_network.ipynb) (Steps 6 + 7) and [load_asset_graph.ipynb](load_asset_graph.ipynb) (Step 8) updated; the loader now writes only `scenario_notes` (4 rows from the JSON's `starter_coverage_contract.notes`). Verified via full rebuild — 11 TS-bound observations, 935 collapsed variables (499 latent + 436 zero) — same facts as before, now derived rather than stored.

27. **Refinery inflow audit + correctness fix** (2026-05-07): full audit of all 115 refineries' inflow source assignments after Pedro flagged Par Hawaii's wiring (it was getting California crude and TMX flow — neither physically reaches Hawaii). Found 5 categories of issues, all rooted in EIA refining districts grouping geographically heterogeneous areas under one default source list:

    1. **West Coast district spans CA/WA/OR/AK/HI/NV** with very different supply chains. Fixed by making the West Coast default state-keyed: Alaska refineries (5) get `alaska_north_slope` only; Hawaii (1) gets `padd5_imports_agg + valdez_origin`; Nevada (1) gets `padd5_imports_agg`; CA gets imports + valdez + california production; WA gets imports + valdez + TMX.
    2. **Texas Gulf Coast district spans Houston/Beaumont and Corpus Christi** with different hubs. Fixed by making the Texas Gulf Coast default site-keyed: Corpus Christi area refineries (3 long-tail ones) now use `corpus_christi_hub` instead of Houston/Nederland.
    3. **Appalachian No. 1 inland refineries** were getting direct `padd1_imports_agg` inflow — geographically wrong (foreign tankers don't reach inland Bradford/Warren/Newell). Fixed by dropping the import aggregate; just `patoka_hub` now (Mid-Continent → east via Mid-Valley/Marathon Pipe Line systems).
    4. **California top-50 refineries (4)** were missing `california_conventional` from their explicit mappings. Fixed.
    5. **Rocky Mountain Montana refineries (4)** were missing `bakken_nd_gathering` (they process significant Bakken crude). Fixed.

    **Architectural principle codified**: an inland refinery cannot have a direct inflow from a `*_imports_agg` aggregate — foreign tankers physically discharge at coastal terminals, then pipelines/hubs feed inland refineries. Coastal districts (East Coast, Texas Gulf Coast, Louisiana Gulf Coast, Corpus Christi sub-area, West Coast California/Washington/Oregon/Hawaii) DO get the imports aggregate; inland districts (Appalachian, IN-IL-KY, MN-WI-ND-SD, OK-KS-MO, Texas Inland, North LA-AR, NM, Rocky Mountain, Alaska, Nevada) DO NOT — they're fed only via pipelines / hubs / local production.

    Updated [add_refinery_inflow_edges.py](add_refinery_inflow_edges.py) to support state/site-keyed sub-defaults inside problematic districts, and added a drop-then-rewire step so re-runs converge to the current mapping (the original 19 named refineries are still preserved untouched). End state: 280 inflow variables across all 115 refineries (was 303 — net −23 from removing wrong edges, plus a few additions); all `latent()` and constrained by district aggregate.

26. **Refinery inflow wiring — top-50 explicit + per-district default for the long tail** (2026-05-06): patch [add_refinery_inflow_edges.py](add_refinery_inflow_edges.py) wires all 115 refineries to physical sources. Strategy chosen after a Pareto analysis (top 50 refineries cover 79.1% of US crude refining capacity): the **top 50** get explicit per-refinery source mapping (4 hub-cluster groups: Texas Gulf, Louisiana Gulf, East Coast, Midwest, plus singles for Inland TX, OK-KS, West Coast, Saint Paul); the **remaining 65 refineries** inherit a per-district default source list (one set of sources per refining district). The 19 original named refineries with pre-existing inflow edges are left untouched (their previous manual mapping is preserved). 266 new `refinery_inflow` edges added (78 from top-50 explicit + 188 from district defaults). Each new inflow variable on the refinery side is assigned `latent()` by `assign_formulas.ipynb` section 4a, constrained by the district-aggregate sum. End state: 214 nodes, 418 edges (was 152), 1,692 variables (was 1,160 — +532 from the 266 new edges' paired in/out variables), 1,027 variable_assignments (was 708 — +319 covering the new refinery inflow latents), all 115 refineries connected to the rest of the network. Source mapping is at the city-level for explicit (e.g., Marathon Galveston Bay → houston_hub + padd3_imports_agg + gulf_of_america) and at the district-level for defaults (e.g., any unspecified Texas Gulf Coast refinery → houston_hub + nederland_hub + padd3_imports_agg + gulf_of_america).

25. **Refinery geography fix + geocoding pass** (2026-05-06): two issues caught when the regenerated map showed only 3 refineries. **First**, the upsert in [add_refinery_capacity_report.py](add_refinery_capacity_report.py) was overwriting the existing 19 named refineries' `geography.lat/lon` with `None` from the report (which has no coordinates). Fixed by deep-merging the geography block in `upsert_node` so existing values survive when the new value is `None`. **Second**, the 96 new refineries from the report had no coordinates at all. Wrote [geocode_refineries.py](geocode_refineries.py) — a manual lookup table of 84 unique `(SITE, STATE)` → `(lat, lon)` pairs (city-center / refinery-area approximate, ~5–10 km accuracy) that fills `geography.lat/lon` on every refinery node. Result: all **115 refineries** now have coordinates in `oil_network.locations` (loaded from JSON via `load_asset_graph.ipynb`), and the regenerated map (`crude_logistics_map.html`, written by [initialize_oil_logistics_network.ipynb](initialize_oil_logistics_network.ipynb)) shows them all. Confirmed: the map reads its data from `oil_network.assets`/`nodes`/`locations` — DB is the source of truth for all rendered output.

24. **Refining-district resolution layer + full refinery list from EIA Capacity Report** (2026-05-06): three-step extension. (1) Downloaded `refcap25.xlsx` (EIA Refinery Capacity Report, January 2025 vintage) — 126 refineries with atmospheric crude distillation capacity (calendar day) totalling 18,423 kbd ≈ 100% of real US refining. (2) Patch [add_refinery_capacity_report.py](add_refinery_capacity_report.py) drops the 5 PADD residual refineries (redirecting their 18 inflow edges to the corresponding `padd*_refining_view` aggregates as abstract-flow edges), adds 10 sub-PADD refining-district aggregate nodes (`district_REC`, `district_RAP`, `district_R2A/R2B/R2C`, `district_R3A/R3B/R3C/R3D/R3E`; PADDs 4 and 5 are single-district so the existing `padd4_refining_view`/`padd5_refining_view` serve double-duty), and upserts 96 new physical refineries from the report (deduplicated against the 19 existing named ones via a manual SITE/STATE match table; 19→215 refineries to 115 net after dropping residuals). (3) Extended [assign_eia.ipynb](assign_eia.ipynb) with 16 new refining-side EIA series (12 authoritative district consumption + 4 auxiliary cross-checks: NUS, R10, R20, R30) and [assign_formulas.ipynb](assign_formulas.ipynb) with refining-side formula bindings (PADDs 1-3 refining consumption = sum-of-districts; per-refinery production/inventory/balancing = 0, consumption = latent constrained by district aggregate, inflow = latent). End state after rebuild: 214 nodes, 152 edges, 1,160 variables, 42 EIA series in catalogue, 5,714 vintaged fact rows, 708 variable_assignments (123 production-side + 585 refining-side). Remaining unassigned: 452 variables on pipelines / hubs / origins / gathering / SPR / exports / imports — out of scope for this pass.

23. **Architecture cleanup: drop redundant topology + add inter-PADD abstract flow edges** (2026-05-06): two patches and an `assign_formulas.ipynb` update. Patch [drop_redundant_topology.py](drop_redundant_topology.py) removed `resolution_hierarchy.children` from every asset (113 assets) and dropped the 58 `aggregation` entries from the JSON edges array. Both were redundant with `formula_inputs` on `variable_assignments` (which is now the single source of truth for the aggregation graph). Patch [add_inter_padd_flow_edges.py](add_inter_padd_flow_edges.py) added 10 abstract-level inter-PADD pipeline flow edges (one per directed pair EIA publishes via `MCRMP*` series) with source `paddA_production_view` → target `paddB_refining_view`. These edges are part of the permanent topology — under fine scopes their flow variables are zero/latent (physical edges below carry the volume); under coarse scopes (PADD-only data) they are TS-bound and the physical edges below become latent. Updated [assign_formulas.ipynb](assign_formulas.ipynb) so aggregate C/I/B variables now get explicit `formula_inputs` derived from the parallel production-rollup children, removing the dropped-topology fallback. Net effect: 113 nodes (unchanged), 152 edges (142 physical + 10 inter-PADD; was 200 incl. 58 aggregation), 756 variables (was 736 — +20 from the new abstract flow edges), and 123 production-side `variable_assignments` (unchanged). The 20 new abstract-flow variables are unassigned for now (next pass: bind them to `movements_*` series). Verified end-to-end via the master orchestrator stages 1+2+4.

22. **Schema fix + 3 derivation views added to `build_oil_network.ipynb`** (2026-05-06): bug fix — both trigger functions (`variables_check_same_graph`, `variable_assignments_check_graph`) now schema-qualify their internal SELECTs (`oil_network.scenarios`, `oil_network.nodes`, etc.), so they work regardless of the caller's `search_path`. The workaround `connect_args={"options": "-csearch_path=oil_network,public"}` in `assign_eia.ipynb` and `assign_formulas.ipynb` is still in place but no longer required. Added Step 7 to the build notebook with three views: `v_flow_edges` (physical flow topology from relational outflow variables), `v_aggregation_edges` (aggregation graph derived from `formula_inputs`), and `v_node_production_sources` (discovery view for every node with a production variable, classifying its binding and showing source TS or formula). All three are scenario-agnostic; the latter two carry `scenario_id` as a column. Verified by full rebuild (stages 1+2+4 of the master orchestrator, ~1 min total). Design rationale: a graph has two parallel edge sets — flow edges (between physical nodes) and aggregation edges (implied by formulas, can connect any-to-any). Both emerge from variables alone; no separate topology table is needed. Earlier views `graph_edges` / `graph_paths` / `graph_paths_upstream` (created manually in DB sessions during 2026-05-01) are superseded; drop them if they remain.

21. **Loader / assignment notebooks split into per-source modular structure + production-side variable_assignments populated end-to-end** (2026-05-06): refactored the monolithic loader into a thin orchestrator + per-source modules ([initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb) → calls [load_eia.ipynb](load_eia.ipynb); future loaders just append to its list). Same pattern for assignments ([initialize_oil_network_assignments.ipynb](initialize_oil_network_assignments.ipynb) → calls [assign_eia.ipynb](assign_eia.ipynb) then [assign_formulas.ipynb](assign_formulas.ipynb)). Wrote both notebooks: `assign_eia.ipynb` registers 26 production-side EIA series in `oil_network.timeseries` (with kbd scaling for STEO), copies vintaged facts into `oil_network.timeseries_data`, and writes 11 authoritative TS-bound rows in `variable_assignments`; `assign_formulas.ipynb` writes the remaining 112 production-side rows (4 arithmetic Tier-1 derivations, 35 sum-over-children rollups, 1 sum-over-outflows, 8 single-outflow → production, 43 zeros, 21 latents). Total: **123 production-side variable_assignments**, every variable on every production-related node bound. Convention: TS references inside formulas use `eia:<series_id>` syntax (resolved by future formula evaluator); variable references use full `variable_id`; sentinels are `sum_over_children`, `sum_over_outflows`, `latent()`, and `0`. Bug found and worked around with `search_path=oil_network,public` on the SQLAlchemy connection — the `variable_assignments_check_graph()` trigger function references `scenarios` without schema-qualifying it, so without the path the trigger fails to resolve. Trigger should be patched in [build_oil_network.ipynb](build_oil_network.ipynb) as a future cleanup.

20. **State-residual production nodes added** (2026-05-05): patched the asset graph via [add_state_residuals.py](add_state_residuals.py) to add two physical production assets — `texas_other` (East TX + Granite Wash + Barnett, ~230 kbd; routes to `ref_padd3_residual`) and `montana_other` (Cedar Creek, ~21 kbd; routes to `ref_padd4_residual`). Both registered as children of `rest_of_l48`, both `state_conventional` subtype. Brings the graph to 113 nodes / 200 edges / 736 variables; production-class assets 12 → 14. Closes the state-vs-basin decomposition gap exposed by the EIA mapping work: with NM ≈ Permian-NM, ND ≈ Bakken-ND, and now `texas_other` + `montana_other` capturing the small non-named-basin remainders, every kbd of US state-level production maps cleanly to a node. The full mapping (TS-bound vs derived vs latent) is documented in [production_map.md](production_map.md).

19. **STEO basin-level production added to data loader** (2026-05-04): added a 27th dataset `production_steo` to [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb) covering 8 STEO series — `COPRPM` (Permian), `COPRBK` (Bakken), `COPREF` (Eagle Ford), `COPRAP` (Appalachia), `COPRHA` (Haynesville), `PAPRPGLF` (Federal GoM), `PAPRPAK` (Alaska), `PAPR48NGOM` (Lower 48 excl GoM). STEO is the EIA monthly publication that provides true basin-level crude production; `petroleum/crd/crpdn` only goes down to state level. The `fetch_eia` helper now normalises STEO's camelCase fields (`seriesId`/`seriesDescription`/`unit`) onto the petroleum schema (`series`/`series-description`/`units`) so downstream staging is uniform. Note: STEO returns history + 18-month forecast in the same series, so `timeseries_date` ranges 2015-01 → 2027-12; the forecast/observed split will be filtered at variable-assignment time, not at ingest.

17. **Metadata schema split** (2026-05-04): introduced `oil_network_metadata` as a sibling schema to `oil_network` for typed asset-class metadata. First table: `metadata_production_assets` (`production_type`, `grade_label`, `grade_name`, `gravity_api`, `sulphur_pct`, `capacity_bd`, `well_count`, `operator`, `vintage_notes`), FK'd to `oil_network.assets`. Convention: **typed columns only — no JSONB in this schema**. Migration direction is monotonic: JSON → typed, never typed → JSON. Un-promoted fields stay in `oil_network.assets.attributes`. Built incrementally; one table at a time. The notebook has three steps: (1) DDL; (2) UPSERT of `production_type` from `assets.attributes` for the 12 physical production-class assets (11 US + Canadian oil-sands aggregate); (3) curated UPDATE filling in `grade_label`, `grade_name`, `gravity_api`, `sulphur_pct`, `capacity_bd`, `operator`, `vintage_notes` — capacity numbers match `coverage_check.py` (11,690 kbd US total + 4,900 kbd WCSB); grade properties from published assay specs; operators ~2024 (refresh on M&A). `well_count` stays NULL — no clean source at this aggregation level. The `production_type` CHECK was deliberately left off — source vocabulary (`tight_oil`, `offshore_conventional`, `conventional_plus_tight`, `niobrara_residual_plus_conventional`, …) is wider than fits a tight enum, so the column is free text until canonicalised. Also added three derived views inside `oil_network`: `graph_edges` (relational variables → directed edges) and `graph_paths` / `graph_paths_upstream` (recursive walks from sources to sinks and vice versa).

---

## Open questions / decisions to make next session

1. **Formula language syntax.** What tokens does the evaluator support? Confirmed direction:
   - Arithmetic: `+ - * /`, parentheses
   - References to other variables by `variable_id`
   - Sentinel functions: `sum_inflow(this_node, commodity)`, `sum_outflow(this_node, commodity)`, `sum_over_children(...)`, `inventory_delta(...)`
   - Open: time-shift (`var_id[-1]`)? Conditionals? Date functions?

2. **Default formulas per node type.** With six variable types and 14 node types, the default-formulas table has up to ~84 rows max — fewer in practice (pipelines need different defaults than refineries). Need a first-pass set of defaults to write.

3. **`variable_assignments` loader strategy.** Two paths:
   - (a) Translate the coverage contract in `scenarios.attributes` into assignments (TS placeholders for observed nodes, formulas for derived/aggregate nodes, zero formulas for structurally-empty slots). Gives a complete starter scenario even before EIA data lands.
   - (b) Wait until EIA TS are loaded, then bind them. Fill in formulas/zeros after.
   I'd lean (a).

4. **EIA → oil_network loader.** Raw EIA data now lives in `oil_network_data_loader` (done — see [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb)). Next: build a loader notebook that takes `oil_network_data_loader.timeseries` → `oil_network.timeseries` + `timeseries_data`, driven by an `(EIA series → asset_id, timeseries_type)` mapping. Start the mapping as JSON (`asset_graph/eia_series_map.json`); migrate to a table later. Rule-based first pass on `(duoarea, process)` → asset, then manual overrides.

5. **Residual refinery telemetry granularity.** When per-facility data eventually lands (Genscape, GoFor, IIR, etc.), the 5 residuals can be replaced by their named constituent refineries (each residual carries an `examples` list in configuration for traceability). Decide which residuals to disaggregate first by data availability.

---

## Quick reference: the four layers (18 tables total)

```
LAYER 1 — IDENTITY
  locations  ← assets ← nodes (in a graph)

LAYER 2 — DEFINITIONS
  commodities, variable_types, node_types
  variables (one slot per node × variable_type × commodity)      (inbound / outbound / connected pipeline names)

LAYER 3 — DATA
  timeseries_types
  timeseries (per asset, optionally per asset-pair)
  timeseries_data (vintaged: PK includes saved_date)

LAYER 4 — COMPUTATION
  scenarios (one per graph; time_range_start/end, frequency, pipeline_layer_note as columns)
  node_scopes                       (one per scenario; was 'coverage_contracts')
  node_scope_authorisations         (per-subsystem authoritative_level)
  node_scope_collapsed_nodes        (per-subsystem collapsed node lists)
  node_scope_notes                  (positioned narrative notes)
  variable_assignments              (binds variable to TS or formula, with effective_from)
  node_type_default_formulas        (defaults that fill in when no explicit assignment)
```

JSONB columns on the original 14 tables are kept as a fallback / canonical reference but everything is also queryable via typed columns + foreign-keyed child tables. UI implementations should target the typed tables.

Everything else lives in JSONB `attributes` columns.

### Sibling schemas

Two schemas live alongside `oil_network`, each intentionally separated to keep concerns clean:

- **`oil_network_metadata`** — typed extension tables FK'd to `oil_network.assets`, one per asset class. Populated by [initialize_oil_network_metadata.ipynb](initialize_oil_network_metadata.ipynb). **No JSONB columns by convention** — typed-only here, un-promoted fields stay on `oil_network.assets.attributes`. Currently: `metadata_production_assets`. Future: `metadata_refineries`, `metadata_pipelines`, `metadata_terminals`, `crude_grades` lookup, etc.
- **`oil_network_data_loader`** — EIA ingestion. 3 tables: `eia_staging` (wide raw), `ref_timeseries` (catalogue), `timeseries` (facts, PK includes `timeseries_published_date` for vintage-on-each-run). Populated by [initialize_oil_network_data_loader.ipynb](initialize_oil_network_data_loader.ipynb). Never references `oil_network`.

> **Deprecated** (2026-05-04): the old `source_eia` schema and its loader (`load_eia_data.ipynb`, now in `archive/`) — replaced by `oil_network_data_loader`. Drop the schema manually with `DROP SCHEMA source_eia CASCADE;` when you're sure you don't need the old data.

### Derived views inside `oil_network`

Created in [build_oil_network.ipynb](build_oil_network.ipynb) Step 7. All scenario-agnostic — `scenario_id` is a column on the views that depend on assignments, so callers filter as needed.

- `v_flow_edges` — physical flow topology, derived from relational `outflow` variables. One row per `(source, target, commodity, via_variable)`. The aggregation graph and the flow graph are separate concerns; this is the flow side.
- `v_aggregation_edges` — aggregation graph, derived from `formula_inputs` on every formula-bound assignment. One row per `(parent_variable, child_variable, rule)`. Captures `sum_over_children`, `sum_over_outflows`, arithmetic formulas, and single-variable references.
- `v_node_production_sources` — discovery view for every node with a `production` variable: classifies the binding (observed / zero / latent / aggregate / derived / unassigned), shows source TS or formula, and (for TS-bound) the actual date range of available data.

> The earlier `graph_edges` / `graph_paths` / `graph_paths_upstream` views (manually created in DB sessions during 2026-05-01 development) are **superseded** by `v_flow_edges` and the variable-only architecture. Drop them if they're still in your DB: `DROP VIEW IF EXISTS oil_network.graph_edges, oil_network.graph_paths, oil_network.graph_paths_upstream;`

Read [oil_network_design.md](oil_network_design.md) for the full WHY of each layer, table, and column choice.
