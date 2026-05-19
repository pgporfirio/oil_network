# CLAUDE.md — Thesis Project Context

> This file is read by Claude Code at the start of every session. It contains the full context of the thesis project, the design principles committed to, the current state of the work, and the immediate next steps. **Read this in full before making any changes.**

---

## 1. Project overview

**Thesis title:** *Asset-Centric Temporal Graphs for Crude-Oil Logistics: Schema Design and Consistency Guarantees*

**Author:** Pedro Porfirio, Master's student at NOVA Information Management School (NOVA IMS), Universidade Nova de Lisboa. Student #20241283.

**Supervisor:** Professor Flavio Pinheiro.

**Planned defence:** early-to-mid 2026.

**What the thesis is about:**

The thesis develops a formal framework for representing crude-oil logistics networks as **asset-centric temporal graphs**, along with formal consistency invariants that guarantee the representation remains internally valid when populated with heterogeneous, multi-resolution data sources. The empirical target is the United States crude-oil supply chain; the data source is public EIA series at monthly frequency.

**Scope decision (April 2026):** the thesis is scoped to the representational framework only. Forecasting using the framework (temporal GNNs, mean-reversion objective, ARIMA/VAR baselines) is explicitly **future work**, not part of this thesis. This was a deliberate scope reduction from an earlier version (v16) that included forecasting as a primary deliverable. The reasoning: the schema work had grown substantial enough to be the full contribution on its own, and doing it thoroughly is more valuable than spreading across both schema and forecasting shallowly.

**Broader context:** Pedro works in oil markets and will deploy the framework at an asset manager trading oil. The thesis is therefore designed with production use in mind — the consistency invariants, the coverage contract mechanism, and the persistent-asset-graph separation exist because they matter operationally, not only academically.

---

## 2. Core design principles — these are committed and must not drift

These are the principles we've worked out over many conversations. They are the foundation of the thesis and must not be relaxed without explicit discussion.

### 2.1 Asset-centric representation

**Every physical asset is a node with its own inventory and its own mass balance.** Pipelines are nodes (with line-fill inventory). Vessels are nodes (with cargo inventory). Storage terminals are nodes. Refineries are nodes. Export and import terminals are nodes. Gathering systems are nodes.

The alternative — location-centric, where pipelines and shipping lanes are edge attributes — is explicitly rejected. In the location-centric view, in-transit crude (pipeline line-fill, vessel cargo) becomes hidden state reconstructible only implicitly from edge attributes. This is ill-suited to commodity logistics, where in-transit volumes are substantial, observable, and operationally meaningful.

### 2.2 Zero-flow edge convention (stable topology)

**Edges are permanent. Flow variables on inactive edges are zero rather than absent.** The adjacency matrix is stable across time, which is what standard GNN architectures require.

This extends to nodes: **inactive nodes also remain in the asset graph.** They are marked as "collapsed" or "null" in the current scenario but persist structurally.

### 2.3 Universal mass balance with balancing item

**Every node satisfies the same mass balance equation, regardless of type:**

```
ΔS(i, g, t) = P(i, g, t) + F_in(i, g, t) − C(i, g, t) − F_out(i, g, t) + B(i, g, t)
```

Where `B` is the balancing item — a first-class node variable that absorbs systematic reporting discrepancies. The term follows IEA practice in national energy statistics. `B` is retained in the data model, not discarded as noise, because it may be structurally informative.

### 2.4 Formula-implies-relation (generalised)

**Edges, partition links, and node status are all derived views of the variables collection.** The variables table is the single source of truth; every graph view is a `CREATE VIEW`. This means:

- The authoritative data model is the **variables collection** plus its `variable_assignments` per scenario.
- `v_flow_edges` — flow topology from relational variables.
- `v_aggregation_edges` — any `formula_inputs` reference.
- `v_partition_tree` — same-type, same-commodity, same-related-node `formula_inputs` only (the partition spine; twelfth-pass view).
- `v_node_status` — `authoritative` / `derived` / `collapsed` per (scenario, node) inferred from a node's variable_assignments (twelfth-pass view, replaces the hand-maintained `starter_status` column).

No structural fact about the graph is stored twice. If the variables collection is consistent, every view is consistent.

### 2.5 Persistent asset graph vs scenario state

**Two layers, separated cleanly:**

- **Persistent asset graph:** the fixed, superset physical topology. Every node and every feasible edge that could ever matter is defined once and only once. Does not change when data sources change.
- **Scenario state:** derived at load time from the asset graph plus the per-scenario `variable_assignments` rows.

Data upgrades **never force a structural change to the asset graph**. Updates to `variable_assignments` regenerate the scenario state, with nodes inferred as authoritative/derived/collapsed by `v_node_status`.

(Earlier framings of this principle introduced a "coverage contract" object as a separate primary structure. Twelfth-pass simplification: the contract is mechanically just the assignment table tagged with a scenario_id. No separate object is needed; the per-variable `timeseries_id`/`formula` choice already encodes per-scenario authoritative/derived/collapsed.)

### 2.6 Physical layer vs observational aggregation layer

**Physical nodes** are real named assets with capacity, configuration, and edges. **Observational aggregates** (PADDs, state-level rollups, basin aggregates when used as rollup views) are **formula-defined views** over physical nodes. They carry no edges in the physical graph. Their sole purpose is to materialise administrative rollups on demand.

### 2.7 Geography metadata ≠ resolution hierarchy

These are two structurally different things and must not be conflated:

- **Geography metadata** is a labelling scheme. Every physical node carries labels (lat/lon, county, state, PADD, country) that many other nodes may share. Labels describe properties, not relationships. Rollups filter on labels.
- **Resolution hierarchy** is a relationship between nodes that represent the same physical system at different granularities (Permian basin ↔ Permian-TX ↔ Permian-Midland-County ↔ individual lease). Assigning data to multiple levels of the hierarchy for the same variable would double-count.

### 2.8 Observed/derived invariant

**The single most important consistency guarantee the framework provides.**

> For each (node, variable, commodity, timestamp), exactly one of `observed_authoritative` (TS-bound) or `derived` (formula-bound) holds. Additional observed series may be attached as `observed_auxiliary`, which are stored on the node but do not participate in the mass balance at their own level.

This invariant is enforced **at the schema level**, not by post-hoc validation. `variable_assignments` carries a CHECK constraint `num_nonnulls(timeseries_id, formula) = 1`. The constraint cannot be violated by an INSERT; it is structural.

The **TS-binding uniqueness audit** (added 2026-05-13) extends this to the TS attribution level: one TS, one variable per scenario. Together they enforce single-attribution at both the variable and TS levels.

**The `formula_inputs` field plays two roles** depending on which side of the invariant the variable sits on:

- When the variable is observed (TS-bound), `formula_inputs` is the **constraint set** — `v_aggregation_consistency` checks `|TS_value − Σ(constituents)|` and flags divergence.
- When the variable is derived (formula-bound), `formula_inputs` is the **definition operand set** — the formula resolves over these inputs.

The dual role is intentional and consistent with Principle 2.4: same field, same data shape, two roles depending on context.

### 2.9 Latent allocation at junctions

**Where flows split across multiple downstream routes (e.g. Permian outflow splitting across Gray Oak, Cactus II, Midland-to-ECHO, etc.) and the per-route split is not observable in public data, the framework does not infer it through ad-hoc rules.** It treats the per-edge flow as a latent quantity constrained by mass balance and capacity ceilings.

Pass-through node types (gathering, pipeline, import_terminal, export_terminal, foreign_export_destination, foreign_production_aggregate) have P = C = ΔS = B = 0 by default, so mass balance forces ΣF_in = ΣF_out at every junction even when individual edges are latent. The `v_node_balance_check` view exposes `sum_out_implied` so the constraint is queryable.

Attention-based temporal graph models (GAT, TGAT) are the natural forecasting approach for these latents — but forecasting is explicitly out of scope for the thesis.

### 2.10 Bidirectional flows as separate edges

**For reversible pipelines and bidirectional terminals (Seaway, Capline, LOOP, SPR sites), each direction is a separate directed edge with its own flow variable.** Zero-flow convention handles the inactive direction. No single bidirectional edge; always two directed edges.

(Earlier framings carried separate principles for "coverage contracts" (2.9), "collapsed state for junctions" (2.10), and a narrower "formula-implies-edge" (2.4). Twelfth-pass simplification merged 2.9 into 2.5 and 2.8; 2.10 into 2.4 — both are now derived views (`v_node_status` and `v_partition_tree`) over the same `variable_assignments` table rather than separate primary structures.)

---

## 3. Thesis style conventions

These are my (Pedro's) preferences that you should respect throughout:

- **British spelling** — "modelled", "organised", "colour", "behaviour" (not modeled/organized/color/behavior).
- **Harvard (author-date) referencing.**
- **Academic prose without AI-signature patterns.** Avoid: tricolons ("clean, robust, and scalable"), self-describing methodology language ("this approach is particularly elegant"), roadmap sentences ("We now proceed to discuss..."), rhetorical summaries, bullet-point lists where flowing prose would be more natural.
- **Prose over bullets.** If content can be expressed in flowing paragraphs, it should be. Reserve bullets for genuine enumerations.
- **Separation of concerns in code.** Data transformation upstream, network construction downstream. All inputs pre-normalised before entering the network builder.

---

## 4. What has been built so far

### 4.1 Thesis documents

**Main document:** `outputs/docs/Master_Thesis_Pedro_Porfirio_v45.docx` (+ matching `.pdf`).

The v45 draft, with Chapters 1–7 fully written and the LP/case-study/conclusion chapters following:

- **Chapter 1:** Introduction, problem statement, research question, scope.
- **Chapter 2:** Literature review (petroleum supply-chain optimisation, graph data models for commodity logistics, energy-statistics conventions, the research gap).
- **Chapter 3:** Domain background — the U.S. crude-oil network (physical layer, PADD structure, production geography, transport network, refining/consumption, what the framework must accommodate).
- **Chapter 4:** Design principles — six axioms and six corollaries (asset-centric representation; stable topology via zero-flow edges; universal mass balance with balancing item; formula-implies-relation; persistent asset graph and scenario state; physical vs observational aggregation layer; geography metadata vs resolution hierarchy; observed/derived invariant; latent allocation at junctions; bidirectional flows as separate directed edges; LOCF carry-forward; node status as a rendering projection).
- **Chapter 5:** Schema and graph construction (two-layer model, starter asset graph, physical/abstract/boundary nodes, variables, partition tree, scenarios and authoritative declarations, end-to-end pipeline).
- **Chapter 6:** The resolver (output table, run flow, dispatch loop, fixed-point loop, bugs encountered and fixed, latent vs unresolved, layered view structure, persistence + audit trail, LOCF).
- **Chapter 7:** Consistency guarantees (schema-level single attribution; partition closure; mass balance at every node; observed-vs-closure-derived B; cross-scenario consistency; operational consistency of the resolver; what the claims do not cover; axiom-to-enforcement mapping).
- Chapters 8–10 cover the LP downstream consumer, the Permian-TX dispatch case study, and conclusion + future work.

**Annexes** (separate documents in `outputs/docs/`):

- `Annex_A_GNN_Primer_v2.docx` (+ `.pdf`) — GNN mechanics primer. Reference material for the forecasting-future-work direction.
- `Annex_B_Graph_Representations_v3.docx` — detailed technical reference on graph representation, zero-flow edges, and mass balance. Section content has been promoted into Chapter 4–7 of the main thesis; Annex B remains the long-form reference.

### 4.2 Asset graph

The starter US crude-oil asset graph is in `asset_graph/`:

- `asset_graph/asset_graph.json` — **authoritative** source of truth. 111 nodes, 198 edges (of which 58 are aggregation edges describing parent/child rollup, not flow). Contains nodes, edges, and starter coverage contract.
- ~~`asset_graph/nodes.csv.old`, `asset_graph/edges.csv.old`~~ — stale flat CSV exports from 2026-04-24, renamed `.old` on 2026-05-06. Not used by anything; the JSON is canonical.

**What's covered:**

- **11 US production physical nodes:** Permian-TX, Permian-NM, Bakken-ND, Bakken-MT, Eagle-Ford-TX, Gulf of America, Alaska North Slope, California-conventional, Oklahoma-conventional, Wyoming-conventional, Colorado-conventional.
- **1 Canadian production aggregate:** `canadian_oil_sands` — single foreign-production node feeding the four cross-border pipelines.
- **9 observational aggregates:** Permian, Bakken, Eagle-Ford, Rest-of-L48 (basin-level); PADD 1–5 production views (wired with aggregation edges to their members); SPR total.
- **5 gathering nodes** (collapsed under starter contract): one per US basin.
- **6 origin terminals:** Midland, Wink, Crane, Johnson's Corner, Three Rivers, Valdez.
- **10 storage hubs:** Cushing (+ 3 collapsed operator sub-terminals), Patoka, Houston, Corpus Christi, Nederland, St. James, Guernsey (Wyoming Rockies aggregator).
- **4 SPR sites + 1 SPR aggregate view.**
- **7 export terminals:** Ingleside, Houston-aggregate (+ 3 collapsed sub-terminals), Nederland, LOOP.
- **4 import terminals:** Clearbrook (Canadian Mainline entry), PADD 1 aggregate, PADD 3 aggregate (Gulf foreign tanker), PADD 5 aggregate.
- **24 pipelines as first-class nodes** — 20 US-internal: Gray Oak, Cactus II, Midland-to-ECHO, Wink-to-Webster, Basin, Longhorn, BridgeTex, DAPL/ETCO, TAPS, Harvest Eagle Ford, EPIC Crude, Kinder Morgan KMCC, Seaway (reversible), MarketLink, Capline, Spearhead, Enbridge Mainline (US-side), LOOP connector, LOCAP, Pony Express. Plus 4 Canadian cross-border: Enbridge Mainline-CA, Keystone, Trans Mountain TMX, Express+Platte.
- **24 refineries** with `capacity_bd`, `nelson_complexity_index`, `operator`, `preferred_slate`, FCC/hydrocracker/coker flags. 19 named (Gulf Coast 5, Midwest 6, West Coast 3, East Coast 2, Rockies 3) + 5 PADD-level residuals tagged `is_residual=true`. Combined modelled refining capacity = 17,484 kbd vs real US ~17,500 kbd (100% coverage).
- **5 refining-centre views** (one per PADD, derived rollups over the named + residual refineries in each PADD).

**Coverage vs real US (2023/24):** production 113%, refining 100%, imports geographic-full incl. Canadian corridor, exports 100%+ headroom, Canadian-pipeline routes ~100%. See `coverage_check.py` for the full breakdown.

**EIA timeseries source data:** raw EIA pull lives in a separate `source_eia` schema, populated by [load_eia_data.ipynb](load_eia_data.ipynb). 26 datasets / 1,485 series / 144,682 vintaged rows from 2015-01-01. Loading from `source_eia` into `oil_network.timeseries` is a separate downstream step (next workstream).

**What's missing:**

- **Vessels** — specified in the schema (Section 3.1.3) but not populated in the starter. Future AIS-data extension.
- **Per-facility data for the 5 PADD residuals** — each residual aggregates ~5–22 unmodelled named refineries; the `examples` list in their `configuration` traces which facilities they cover so the residual can later be replaced by named children when per-facility data lands (Genscape, GoFor, IIR, etc.).

### 4.3 Coverage contract for the starter scenario

Stored inside `asset_graph.json` under `starter_coverage_contract`. Specifies authoritative levels for each subsystem:

- Permian, Bakken: authoritative at basin aggregate (EIA publishes only basin-level).
- Eagle Ford: authoritative at state sub-basin (single state, so equivalent to basin).
- Gulf of America, Alaska: authoritative at physical node level.
- Rest of L48: authoritative at residual aggregate (EIA publishes only a residual total).
- SPR: authoritative at site level (EIA PSM Table 38 publishes site-level data monthly).
- Cushing: authoritative at hub level; operator sub-terminals collapsed.
- Houston export: authoritative at aggregate level; facility sub-terminals collapsed.

---

## 5. Resolved issues from earlier handover

The earlier handover (24 April 2026) flagged four structural issues with the JSON-only asset graph. All four are now resolved by the Postgres migration:

### 5.1 ✓ Aggregation edges separated from flow edges

The 58 aggregation edges in `asset_graph.json` describe parent/child rollup, not flow. The loader (`load_asset_graph.ipynb`) explicitly skips them when creating relational variables, so they live in the JSON as metadata for the resolution hierarchy but never enter the variables table or affect mass-balance computations.

### 5.2 ✓ Edges derived from relational variables

In Postgres, the canonical edge representation is the pair of relational variables (one `outflow` on the source, one `inflow` on the target) created from each non-aggregation JSON edge. The JSON `edges` array remains a convenience input format. A SQL view `edges` over `variables WHERE related_node_id IS NOT NULL` gives the derived edge list on demand.

### 5.3 ✓ Resolution hierarchy single-sourced

The hierarchy still lives in `assets.attributes.resolution_hierarchy` on each node (parent + children), and aggregation edges in the JSON are loaded to the same shape. Both representations are kept consistent at load time. When variable-level rollup formulas land, they will derive from the same hierarchy.

### 5.4 ✓ Starter status: variable-level via assignments

The schema's `variable_assignments` table moves status to the per-variable level: each (variable, scenario, effective_from) row binds either a `timeseries_id` (observed) or a `formula` (derived). The node-level `starter_status` field in JSON is now a summary used by the loader to decide which assignment to write at load time. Per-variable overrides are supported by the schema; populating them is the next workstream.

---

## 6. Where the work stands (next workstream)

The Postgres schema is built and the asset graph is loaded with full coverage. Immediate next workstream is `variable_assignments` + EIA timeseries ingestion + the formula evaluator runtime. See [NEXT_STEPS.md](NEXT_STEPS.md) for the action items in order, and [HANDOVER.md](HANDOVER.md) for resume-here instructions on a fresh machine.

The historical note about the schema-design decision follows for reference.

### 6.0 (historical) Why we moved JSON → Postgres

This decision was made early in the session and is now done. Reasons:

1. The data model we've converged on is intrinsically relational (nodes, variables, formulas, coverage contracts, time series, provenance).
2. Time series data is coming next (EIA monthly series over 10 years across 70+ variables), which JSON handles poorly.
3. The validation experiments in Chapter 5 are queries, which are natural in SQL.
4. Pedro's existing infrastructure already includes PostgreSQL tables (`network_nodes`, `network_edges`, `network_variables`) — this is a natural extension of his existing setup.
5. The `UNIQUE` constraint on variables makes the observed/derived invariant (principle 2.8) enforceable at the schema level, not just as a runtime check.

### 6.1 Schema design principles for the Postgres tables

Three layers, roughly 8–10 tables.

**Layer 1 — asset graph (static):**

- `nodes` — identity only: `node_id`, `name`, `node_class`, `node_subtype`
- `node_geography` — labels: lat, lon, state, county, padd, country, sea
- `node_configuration` — class-specific features in `jsonb` (operator, capacity, diameter, nelson_index, etc.)

**Layer 2 — variables (the data model):**

- `variable_types` — dimension: P, C, S, F_in, F_out, B, phi; with `is_relational` flag
- `products` — dimension: crude, gasoil, gasoline, etc. (starter: just crude)
- `variables` — one row per variable instance:
  - `variable_id`, `variable_type` FK, `product` FK, `node_id` FK, `ref_node_id` FK NULLABLE (for phi)
  - `definition` enum(`observed`, `derived`, `zero_by_construction`)
  - `formula` text NULLABLE (e.g. "sum_over_children(P)")
  - `formula_inputs` text[] — list of variable_ids this depends on
  - `UNIQUE(variable_type, product, node_id, ref_node_id)` — enforces invariant

**Layer 3 — scenarios and time series:**

- `coverage_contracts` — scenario definitions
- `contract_authorisations` — per-subsystem authoritative declarations
- `variable_values` — time series data with provenance (`variable_id`, `observation_date`, `value`, `unit`, `source`, `source_release_date`, `resolution_level`)
- `scenarios` — materialised scenario graphs
- `scenario_variable_status` — per-variable status under each scenario

### 6.2 Key derived views (not tables)

These fall out of the variables collection naturally:

```sql
-- Edges are a view over relational variables
CREATE VIEW edges AS
SELECT node_id AS source, ref_node_id AS target, variable_id AS from_variable
FROM variables WHERE ref_node_id IS NOT NULL;

-- Resolution hierarchy is derivable by parsing aggregation formulas,
-- or more cleanly stored as a closure table refreshed when variables change.
```

### 6.3 Approach

1. **Start with SQLite as a development target.** Portable, file-based, no server required. Write DDL that is SQLite-compatible but also works on Postgres with minimal adjustment (document any differences explicitly).
2. **Build and test the full migration end-to-end in SQLite** — schema, migration script, query examples, invariant tests.
3. **Port to Postgres** when SQLite validates cleanly. The DDL differences are small: enum types, text[] arrays, jsonb vs TEXT with JSON. All manageable with a single DDL file that has sectioned `-- SQLITE:` vs `-- POSTGRES:` comments.
4. **Validate the invariants.** Write specific tests that (a) a duplicate-authoritative insert is rejected, (b) a parent-child double-assignment is detected, (c) the PADD rollup query returns the same value under different coverage contracts when the underlying data is consistent.

### 6.4 What NOT to do

- **Do not populate time series yet.** The schema must come first. EIA data pulls come later, after the schema is proven.
- **Do not build a scenario loader yet.** Schema first, then data, then loader. The loader is Chapter 4's deliverable and needs the database as a substrate.
- **Do not move to Postgres prematurely.** SQLite validates the design cheaply. Move when SQLite is boring.
- **Do not discard the JSON asset graph.** Keep it as the canonical input format for the migration script. It's a useful artefact.

---

## 7. Broader roadmap beyond the database migration

After the database is in place:

1. **Add the refinery layer** to the asset graph (in the database now, not JSON). 5 refining-centre aggregate nodes plus sub-node placeholders for individual refineries.
2. **Pull EIA monthly data** for January 2015 through December 2025. The EIA Open Data API v2 is the primary source; some data needs scraping from PSM tables. Run the fetch from Pedro's local machine (sandbox environments can't reach `api.eia.gov`).
3. **Implement the scenario loader.** Deterministic pipeline: reads assignment table + coverage contract → emits scenario graph with provenance.
4. **Run Chapter 5 validation experiments:**
   - **Claim 1:** Same scenario under three progressively finer coverage contracts; verify consistency.
   - **Claim 2:** Invariant rejects deliberately double-counted assignments; compare with naive schema.
   - **Claim 3:** Collapsed state handles mixed-resolution data without mass-balance degradation.
   - **Claim 4:** Balancing item captures operationally meaningful signal (Hurricane Harvey 2017, COVID 2020, SPR 2022 releases, Colonial Pipeline 2021 — map events against B series).
5. **Complete Chapter 6** — worked case study of the implementation.
6. **Complete Chapter 7** — discussion, limitations, future work (including forecasting as the natural extension).

---

## 8. Practical workflow notes

### 8.1 DOCX editing

Pedro has an established DOCX editing workflow for the thesis:

- Unpack with `python unpack.py <file>.docx` (creates `unpacked/` directory with raw XML)
- Edit `unpacked/word/document.xml` directly
- Repack with `python pack.py unpacked/ <output>.docx --original <source>.docx` — the `--original` flag is required
- `w14:paraId` values must be valid 8-char hex below `0x7FFFFFFF`
- Bullet list conversion requires removing `<w:pStyle w:val="ListParagraph"/>` and `<w:numPr>` blocks, replacing with a single prose paragraph

For small edits, directly use `str_replace` on the XML. Anchor replacements to unique surrounding elements (section headings) for reliability.

### 8.2 Pedro's working style

- **Iterative approval**: verbal confirmation before document edits are applied.
- **Latest document is retained**: expects the most recent thesis version to be picked up without re-uploading.
- **Versioned increments**: v4.0 → v4.1 → ... → v16+ for thesis; v1 → v2 for asset graph.
- **Asks direct questions about design choices**. Answer them directly, don't hedge. If the framework has a clear answer, give it. If multiple options exist, lay them out with trade-offs.
- **Pushes back on drift.** When the framework says X and I've been doing Y, Pedro notices. Respect this — check your work against the principles in Section 2 before producing anything.

### 8.3 Things to remember

- Pedro works in oil markets. Domain terminology like "basin," "crack spread," "line-fill," "PADD," "slate," "Nelson Complexity," "VLCC" is familiar to him. Use it without jargon-explaining.
- Pedro is building this for production use at an asset manager. Forward-compatibility, auditability, and provenance are not academic concerns — they are practical requirements.
- The thesis framing and the production framing should not conflict. Both are served by the same rigorous design.

---

## 9. Files in this directory (Stage 2 layout)

```
Stage2/                                 ← repository root (this directory)
├── CLAUDE.md                           ← root entry point for Claude Code
├── README.md
├── setup.ipynb                         ← fresh-machine bootstrap
├── requirements.txt
├── .env.example                        ← template (copy to .env and fill in)
├── .gitignore
│
├── claude/                             ← project memory (you are here)
│   ├── CLAUDE.md                           (this file — read first)
│   ├── HANDOVER.md                         (resume-here doc; pass-by-pass history)
│   ├── PROJECT_STATE.md                    (current numbers, refreshed after major changes)
│   ├── NEXT_STEPS.md                       (immediate action items)
│   ├── NOTEBOOKS.md                        (orchestrator chain documentation)
│   ├── DESIGN_PRINCIPLES.md                (standalone copy of Section 2 for easy reference)
│   ├── DATA_MODEL_PREMISES.md              (the 30 design premises, full prose)
│   ├── SCENARIO_CONSTRUCTION.md            (five-stage scenario construction reference)
│   ├── RESOLVER_WALKTHROUGH.txt            (guided reading of resolve_scenario.py)
│   ├── time_log.md                         (working-session log)
│   └── OFFLINE_NOTES.txt / .pdf            (offline working notes)
│
├── code/                               ← all active Python + notebooks (flat)
│   ├── paths.py                            ← single source of truth for filesystem locations
│   ├── network_graph.py                    ← NetworkGraph engine (read + future write API)
│   ├── resolve_scenario.py                 ← the resolver (auto-refreshes analytic views)
│   ├── recursive_resolver.py               ← fixed-point alternative resolver
│   ├── compare_resolvers.py                ← diff resolve_scenario vs recursive_resolver
│   ├── verify_state.py                     ← one-shot sanity check after a rebuild
│   ├── refresh_views.py                    ← --structural / --analytic mat-view refresh
│   ├── regenerate_htmls.py                 ← orchestrator (--force / --list)
│   ├── render_utils.py                     ← metadata beacons + audit recording
│   ├── pdf_utils.py                        ← shared reportlab helpers
│   ├── make_partition_map.py               ← renderer
│   ├── make_node_neighbors_map.py          ← renderer
│   ├── make_balance_resolver_ui.py         ← renderer
│   ├── make_hierarchy_resolver_ui.py       ← renderer
│   ├── make_map_resolver_ui.py             ← renderer
│   ├── make_balance_ui.py                  ← template provider (HTML/JS templates)
│   ├── make_hierarchy_explorer.py          ← template provider
│   ├── make_map.py                         ← template provider
│   ├── pdf_design_principles.py            ← writes to outputs/docs/
│   ├── pdf_resolver_walkthrough.py         ←      "
│   ├── pdf_scenario_construction.py        ←      "
│   ├── pdf_graph_construction.py           ←      "
│   ├── audit_capacity_violations.py        ← capacity audit
│   ├── audit_resolution_anomalies.py       ← LOCF / negative-derived audit
│   ├── create_*.py / populate_*.py / init_resolver_tables.py  ← DDL + seed helpers
│   ├── build_fig_3_1.py                    ← thesis Figure 3.1 renderer
│   ├── migrations/                         ← 23 one-shot scripts (numbered passes, add_*, repoint_*, split_*, wire_*, refactor_*, promote_*, patch_*)
│   ├── load_asset_graph.ipynb              ← initial DB load
│   ├── load_eia.ipynb                      ← EIA TS load into oil_network.timeseries_data
│   ├── assign_eia.ipynb                    ← TS-bound variable assignments
│   ├── assign_formulas.ipynb               ← formula-bound variable assignments
│   ├── build_oil_network.ipynb             ← schema DDL
│   ├── initialize_oil_*.ipynb              ← orchestrator notebooks (master + 4 stages)
│   ├── resolver.md                         ← resolver design notes
│   └── data/refcap25.xlsx                  ← reference data
│
├── config/
│   └── asset_graph.json                    ← the seed file (loaded once by load_asset_graph.ipynb)
│
└── outputs/
    ├── html/                               ← 5 canonical HTMLs, each with metadata beacon
    │   ├── oil_network_partition_map.html
    │   ├── oil_network_node_neighbors.html
    │   ├── oil_network_balance_resolver.html
    │   ├── oil_network_hierarchy_resolver.html
    │   └── oil_network_map_resolver.html
    └── docs/                               ← thesis docs, annexes, PDFs, diagrams
        ├── Master_Thesis_Pedro_Porfirio_v45.docx / .pdf  (current)
        ├── Annex_A_GNN_Primer_v2.docx / .pdf
        ├── Annex_B_Graph_Representations_v3.docx
        ├── Design_Principles.pdf
        ├── Scenario_Construction.pdf
        ├── Resolver_Walkthrough_v2.pdf
        ├── Graph_Construction.pdf
        ├── Crude flow.svg / drawio + Cushing / Midland / Patoka / StJames flow diagrams
        ├── data_model.png / .svg / schema_map.svg / fig_3_1_physical_supply_chain.png
        ├── figures/                            (thesis figures rendered by build_fig_3_1.py et al.)
        └── References/                         (cited papers in PDF)
```

**Path-resolution contract:** any script that writes a file imports from `paths.py`. Renderers write to `HTML_DIR`; PDF generators write to `DOCS_DIR`; `load_asset_graph` reads `ASSET_GRAPH_JSON` from `CONFIG_DIR`. No script computes filesystem paths by hand; relocating the project is a one-line edit to `paths.py`.

---

## 10. If there is any ambiguity

When in doubt:

1. Re-read Section 2 (design principles). Almost every ambiguity is resolved by one of them.
2. Check what Annex B says on the specific topic — it's the technical reference.
3. Ask Pedro before making a design decision that could drift from the framework. Small implementation choices are fine to proceed with; architectural choices should be confirmed.
4. When implementing, prefer the approach that matches the framework most faithfully, even if it's slightly more work. The invariant-enforcing approach always wins over post-hoc validation.
