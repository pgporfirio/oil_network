# Data model — premises and rationale

A single reference covering every design premise behind the asset graph and the `oil_network` schema. For each premise: **what it says**, **why we adopted it**, and **an example from the live graph**.

The premises break into three groups:

| Group | What it covers | Premises |
|---|---|---|
| **A. Framework** | What the graph represents conceptually | A1 – A12 |
| **B. Schema** | How the data is organised in JSON + Postgres | B1 – B11 |
| **C. Operational** | How scenarios, assignments, and mass balance behave at run time | C1 – C7 |

Cross-references: [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) is the short-form companion; [oil_network_design.md](oil_network_design.md) is the detailed schema reference; CLAUDE.md §2 is the in-context restatement of the framework principles.

---

## A. Framework premises

### A1. Asset-centric representation

**Premise.** Every physical asset is a node with its own inventory and its own mass-balance equation. Pipelines, vessels, terminals, refineries, hubs — all nodes. The alternative — location-centric modelling, where pipelines and shipping lanes are *edge attributes* — is rejected.

**Why.** In commodity logistics, in-transit volumes (pipeline line-fill, vessel cargo) are substantial, observable, and operationally meaningful. If pipelines are edges, that volume becomes hidden state, reconstructible only implicitly. Treating each pipeline as a node makes line-fill a first-class state variable, which is required for accurate mass balance over time.

**Example.** `pipe_enbridge_mainline` is a node, not an edge. Its `configuration.line_fill_kbbl = 3,200` records the inventory of crude in the pipe at any moment. Its `inventory` variable changes over time as crude flows in at Clearbrook (`pipeline_intake` edge from `clearbrook_entry`) and out at Patoka and Cushing (`pipeline_outflow` edges).

### A2. Stable topology via the zero-flow convention

**Premise.** Edges are permanent. When a route is inactive at a given time, its flow variable is *zero*, not absent. The adjacency matrix is fixed across time. Inactive nodes also remain in the asset graph — marked `collapsed`, but never deleted.

**Why.** Standard GNN architectures assume a stable adjacency. A graph whose edges appear and disappear with data availability is structurally unfit for representation learning. Zero-flow edges keep the topology fixed while the *values* of the flow variables change over time.

**Example.** `pipe_seaway` is a reversible pipeline modelled as **two** directed edges: `cushing_hub → pipe_seaway → houston_hub` (southbound — the dominant post-2014 direction) and `houston_hub → pipe_seaway → cushing_hub` (northbound — historic, selectively active). Both edges always exist; in months when northbound flow is zero, the corresponding outflow variable simply takes the value 0.

### A3. Universal mass balance with a balancing item

**Premise.** Every node, regardless of type, satisfies one equation:

```
ΔS(i, g, t) = P(i, g, t) + F_in(i, g, t) − C(i, g, t) − F_out(i, g, t) + B(i, g, t)
```

`B` is the balancing item — a first-class node variable that absorbs systematic reporting discrepancies. It follows IEA practice in national energy statistics.

**Why.** A single equation simplifies the framework and the loader. Embedding a balancing item rather than discarding it means the model never silently violates mass balance — discrepancies surface as a quantifiable residual that can be analysed (see Chapter 5 Claim 4: the B-series should respond meaningfully to events like Hurricane Harvey 2017 or the 2022 SPR releases).

**Example.** `cushing_hub` carries six variables: `production` (= 0 by construction; Cushing produces nothing), `consumption` (= 0; Cushing consumes nothing), `inventory` (the EIA weekly stocks number), `inflow` per upstream pipeline, `outflow` per downstream pipeline, and `balancing_item` (the residual after all the others — typically near zero, but spikes during operational anomalies).

### A4. Formula-implies-edge

**Premise.** Edges are never declared independently. They emerge from variable formulas. An edge `(i, j, g)` exists if and only if there exists a variable `v(i, g) = f(…, v(j, g), …)`. The authoritative store is the **variables collection**; the edges list is a derived index.

**Why.** Single source of truth. Trying to keep an `edges` table consistent with a `variables` table by hand is fragile — they will drift. Deriving edges from variables means the topology is automatically correct.

**Example.** When the loader reads `asset_graph.json` and sees the edge `houston_hub → ref_exxon_baytown`, it does **not** insert into a separate `edges` table. Instead it creates two paired rows in `variables`: an `outflow` on Houston (`node_id=houston_hub, related_node_id=ref_exxon_baytown`) and an `inflow` on Baytown (`node_id=ref_exxon_baytown, related_node_id=houston_hub`). The edge appears as a SQL view `SELECT node_id AS source, related_node_id AS target FROM variables WHERE related_node_id IS NOT NULL AND variable_type='outflow'`.

### A5. Persistent asset graph vs scenario graph

**Premise.** Two layers, separated cleanly. The **persistent asset graph** is the fixed, superset physical topology — every node and every feasible edge that could ever matter, defined once and only once. The **scenario graph** is derived at load time from the asset graph plus an assignment table plus a coverage contract. Data upgrades never force a structural change to the asset graph.

**Why.** Schema stability. When EIA changes a series ID, when a new operator-level data feed becomes available, when a refinery is sold to a new operator — the asset graph stays the same; the scenario graph regenerates. Without this separation, every data upgrade would mean a schema migration.

**Example.** When the Genscape per-operator Cushing storage feed lands in two years' time, the three Cushing operator sub-terminals (`cushing_enbridge`, `cushing_plains`, `cushing_enterprise`) — which today have `starter_status = "collapsed"` and zero flow — will have their starter_status promoted to `authoritative` in a new coverage contract. The asset graph itself doesn't change.

### A6. Physical layer vs observational aggregation layer

**Premise.** **Physical nodes** are real named assets with capacity, configuration, and edges. **Observational aggregates** (PADDs, basin rollups when used as views, refining-centre views) are *formula-defined views* over physical nodes. They carry no edges in the physical graph. Their sole purpose is to materialise administrative rollups on demand.

**Why.** A PADD is a US-government statistical region, not a piece of infrastructure. Allowing PADDs to carry physical edges would smear administrative geography into the topology, which is wrong. Treating them as derived views keeps the graph clean.

**Example.** `padd3_refining_view` (Gulf Coast refining centre) has `starter_status = "derived"`, `node_class = "observational"`, and aggregation edges (no flow edges) to its 5 named refineries + 1 residual. Its `consumption` variable is a formula: `sum_over_children(consumption)`. The view is queryable but never carries a flow.

### A7. Geography metadata ≠ resolution hierarchy

**Premise.** Two structurally different things, never conflated:

- **Geography metadata** is a labelling scheme. Every physical node carries labels (lat/lon, county, state, PADD, country) that many other nodes may share.
- **Resolution hierarchy** is a relationship between nodes that represent the same physical system at different granularities (Permian basin ↔ Permian-TX ↔ Permian-Midland-County ↔ individual lease).

**Why.** Mixing them produces double-counting. If `padd3_production_view` claims as members both `permian_tx` (via geography filter) AND `permian` (via resolution hierarchy), summing the production gives 2× the right answer.

**Example.** `permian` (basin aggregate) is in the **resolution hierarchy** above `permian_tx` (state sub-basin). `permian_tx` is also tagged with **geography** `padd = "PADD3"`. The `padd3_production_view` aggregation edges point to `permian_tx`, *not* to `permian` — selecting one level cleanly.

### A8. Observed / derived invariant

**Premise.** For each `(node, variable, commodity, timestamp)`, exactly one of `observed_authoritative` or `derived` holds. Additional observed series may be attached as `observed_auxiliary` — stored on the node but not participating in the mass balance at their own level. Enforced **at the schema level**, not by post-hoc validation.

**Why.** This is the single most important consistency guarantee the framework provides. Aggregation double-counting cannot arise silently; it either throws at insert time or is caught by the scenario loader's invariant check. Post-hoc validation always misses cases — schema-level enforcement is bulletproof.

**Example.** In Postgres, the `variables` table has `UNIQUE(variable_type, commodity, node_id, related_node_id)`. Trying to insert two rows for `(production, crude, permian, NULL)` fails immediately with a unique-constraint violation. The framework guarantees that Permian production cannot accidentally be observed twice (e.g., once at the basin level and once via state-level disaggregation).

### A9. Coverage contracts

**Premise.** Per-scenario declarations of which resolution level is authoritative for each subsystem. The contract tells the scenario loader: *"for the Permian subsystem, the basin aggregate is authoritative, and sub-basin nodes are collapsed."* When finer data arrives later, the contract is updated, the scenario regenerates, and the asset graph is unchanged.

**Why.** Decouples data availability from structure. Without coverage contracts, every change in data granularity would mean a schema or graph change.

**Example.** The starter contract has:
```json
"permian_subsystem": {
  "level": "permian",
  "collapsed_below": ["permian_tx", "permian_nm",
                      "permian_tx_gathering", "permian_nm_gathering"]
}
```
EIA publishes only basin-level Permian production, so `permian` is authoritative. The day a state-level series becomes available, the contract flips to authoritative-at-state-level and `permian_tx` / `permian_nm` get promoted; the basin aggregate becomes a derived view.

### A10. Collapsed state for junction nodes

**Premise.** Junction / gathering / pump-station nodes that physically exist but whose variables cannot be distinguished from their neighbours at the current data resolution are `collapsed`. They remain in the asset graph; their flows pass through as formulas from authoritative upstream nodes to authoritative downstream nodes.

**Why.** Removing them when data is coarse, then re-adding when data is finer, would break the stable-topology premise. Collapsing keeps them present but inert.

**Example.** `cushing_enbridge`, `cushing_plains`, `cushing_enterprise` are physically real operator-owned tank farms inside the Cushing complex. The starter contract collapses them under their parent `cushing_hub`; in the source-sink audit, they correctly show "<<no source — collapsed sub-terminal under the starter contract; flows route via parent>>". They will reactivate when per-operator Genscape feeds land.

### A11. Latent allocation at unobservable splits

**Premise.** Where flows split across multiple downstream routes (e.g. Permian outflow splitting across Gray Oak, Cactus II, Midland-to-ECHO, BridgeTex, Longhorn) and the per-route split is not observable in public data, the framework does not infer it through ad-hoc rules. It treats the per-edge flow as a *latent* quantity constrained by mass balance and capacity ceilings.

**Why.** Ad-hoc allocation rules (e.g., "split proportional to capacity") embed assumptions that may be wrong and that downstream models cannot easily inspect or override. Marking the flows latent makes the uncertainty explicit and addressable later (attention-based temporal GNNs are the natural forecasting approach — but forecasting is future work).

**Example.** `midland_origin` connects to four pipelines: Gray Oak (900 kbd cap), Cactus II (670 kbd), Midland-to-ECHO (650 kbd), Wink-to-Webster (600 kbd, via Wink), Basin (550 kbd). The total capacity is ~3.4 mb/d. Real Permian-TX production is ~4.6 mb/d; the per-pipeline split is not in EIA. Each per-pipeline outflow is a separate variable, capacity-constrained but not deterministically allocated.

### A12. Bidirectional flows as separate edges

**Premise.** For reversible pipelines and bidirectional terminals (Seaway, Capline, LOOP, SPR sites) each direction is a separate directed edge with its own flow variable. Zero-flow convention handles the inactive direction. No single bidirectional edge; always two directed edges.

**Why.** A directed graph keeps the mass-balance bookkeeping simple — every flow has an unambiguous source and target. A bidirectional edge would require treating its sign as a state variable, complicating both the schema and any downstream analysis.

**Example.** Seaway is modelled as two pipeline_intake / pipeline_outflow pairs:
- `cushing_hub → pipe_seaway → houston_hub` (southbound, cap 850 kbd, dominant)
- `houston_hub → pipe_seaway → cushing_hub` (northbound, cap 400 kbd, historic)

Same with SPR sites: every SPR site has `spr_fill` (commercial → SPR) and `spr_release` (SPR → commercial) as separate directed flow pairs.

---

## B. Schema premises

### B1. Asset = identity (physical or abstract)

**Premise.** Assets are the universal identity layer. Anything we name and refer to consistently — physical (Permian basin, refinery, pipeline) or abstract (PADD 5, regulatory aggregations, futures benchmarks) — is an asset. The `assets.attributes.kind` JSONB field carries `"physical"` or `"abstract"`.

**Why.** Avoids parallel hierarchies (assets-vs-locations-vs-regions). Single identity layer simplifies time series and variable assignments — both reference assets without forking semantics.

**Example.** `padd5_refining_view` is an asset with `kind = "abstract"`. `ref_marathon_la` is an asset with `kind = "physical"`. Both have a `location_id`, both are referenced from `nodes`, both can in principle have time series — nothing structurally distinguishes them beyond the metadata flag.

### B2. Node = role of an asset in a graph

**Premise.** A node is the appearance of an asset in a particular graph. `nodes(node_id, graph_id, asset_id, node_type, ...)` with `UNIQUE(graph_id, asset_id)`. The same asset can appear in multiple graphs as different nodes.

**Why.** Different graphs may model the same asset differently. A refinery in a "blackbox" graph might be a single sink; in a "slate" graph it might be split into a feedstock-mix node, a unit-operation graph, and a yield-vector node. Tying variables to nodes (not directly to assets) lets each graph have its own variable set and balance equations per asset.

**Example.** `ref_motiva_port_arthur` appears as a single node in our `us_crude_starter` graph. In a future `us_crude_slate` graph it would appear as `ref_motiva_port_arthur::heavy_sour_intake`, `ref_motiva_port_arthur::fcc_unit`, etc. — multiple nodes for the same asset, each with its own variables.

### B3. Variables are slots, not data

**Premise.** A variable is the abstract mass-balance slot at a node — `(variable_type, commodity, node_id, related_node_id?)`. It carries no values, no formula, no scenario state. Just structure.

**Why.** Schema separation. The variable defines *what* it represents; the assignment defines *how* it gets a value. The slot stays stable while different scenarios provide different sources.

**Example.** The variable row `outflow__crude__permian_tx_gathering__midland_origin` exists once in the `variables` table. In the starter scenario, it is bound (via `variable_assignments`) to a formula derived from EIA basin-level Permian production split by operator capacity heuristic. In a future operator-resolution scenario, the same variable_id is bound instead to a Genscape direct measurement.

### B4. Variable types are universal labels

**Premise.** Six labels: `production`, `consumption`, `inventory`, `balancing_item`, `inflow`, `outflow`. Just labels — no `balance_sign` column, no special role for any one type, no flag distinguishing relational from non-relational.

**Why.** Embedding `balance_sign` would hardcode the universal mass-balance equation. Different node types might have different balance equations (a boundary node, for example, may have none). The equation lives as a per-node formula in `variable_assignments`, not in `variable_types`.

**Example.** A pipeline node has `inventory` (line-fill) and `inflow`/`outflow` per endpoint, but no `production` or `consumption`. A refinery node has `consumption` (refinery runs) and `inflow` per upstream feeder, but `outflow = 0` for crude (refineries consume crude; their output is products which are a different commodity). Both fit the same six universal types.

### B5. Inflow / outflow are relational; the others are not

**Premise.** Variable types `inflow` and `outflow` MUST have `related_node_id`; the other four MUST NOT. A CHECK constraint enforces this.

**Why.** Per-edge flows are the natural representation of a directed network. Total node-level inflow / outflow is computed by summing the per-edge variables — no separate `F_in_total` slot is needed.

**Example.** `cushing_hub` has multiple `inflow` rows (one per upstream pipeline: Basin, Spearhead, Marketlink, Seaway northbound, Enbridge Mainline, Pony Express, Keystone, plus Oklahoma direct), each with its own `related_node_id`. The "total inflow at Cushing" is `SUM(value)` over those rows — it is not a separately-stored variable.

### B6. Time series at asset level (graph-agnostic)

**Premise.** A time series describes one measurement at one asset (`asset_id`) — and optionally a relational pair (`related_asset_id`) for pipeline flows, vessel voyages, inter-terminal transfers. It carries no graph context.

**Why.** EIA series, vessel telemetry, commercial data services — all describe real-world subjects, not graph positions. The same TS can feed multiple graphs through their respective node-level variables.

**Example.** EIA's series `PET.MCRRIP31.M` (PADD 3 refinery input) is a timeseries on the asset `padd3_refining_view`. In the `us_crude_starter` graph it binds to `consumption__crude__padd3_refining_view`. In a hypothetical alternative graph that doesn't have `padd3_refining_view` (e.g., a slate-resolution graph), the same TS would be unused — but the row in `timeseries_data` doesn't change.

### B7. Aggregation edges describe rollup, not flow

**Premise.** The JSON `edges` array contains `aggregation`-typed edges that describe parent → child rollup (e.g., `permian → permian_tx`, `padd3_refining_view → ref_motiva_port_arthur`). These are *deliberately not* loaded as relational variables. They live in the JSON as metadata for the resolution hierarchy.

**Why.** Aggregation is a formula, not a flow. Permian doesn't physically *flow into* Permian-TX; the aggregate IS the sum of the constituent. Treating aggregation as flow would violate premise A4 (formula-implies-edge) and create phantom edges in the GNN adjacency.

**Example.** `e_padd3_refining_view__ref_motiva_port_arthur__aggregation` exists in `asset_graph.json.edges` with `edge_type = "aggregation"`. The loader's relational-variable creation step explicitly skips it (`if e["edge_type"] == "aggregation": agg_skipped += 1; continue`). The variable `outflow__crude__padd3_refining_view__ref_motiva_port_arthur` does **not** exist in the database.

### B8. Bidirectional edges = two pipeline_intake / pipeline_outflow pairs

**Premise.** For a reversible pipeline node, the JSON has both directions encoded as separate edges feeding the same pipeline node, with separate intake and outflow edges per direction.

**Why.** Same as A12 but at the schema level — the loader doesn't need any special-case logic for "bidirectional" because each direction is encoded as ordinary unidirectional edges.

**Example.** For `pipe_seaway` (reversible), the JSON has four edges:
- `cushing_hub → pipe_seaway` (intake, southbound)
- `pipe_seaway → houston_hub` (outflow, southbound)
- `houston_hub → pipe_seaway` (intake, northbound)
- `pipe_seaway → cushing_hub` (outflow, northbound)

The variables table ends up with two `inflow` and two `outflow` rows on `pipe_seaway` — one for each direction.

### B9. Loader derives `kind` from `node_class`

**Premise.** The loader does not maintain a hardcoded list of "abstract" subtypes. Instead it derives `kind = "abstract"` if and only if `node_class == "observational"`.

**Why.** Originally the loader had a hardcoded set `OBSERVATIONAL_SUBTYPES = {"observational_aggregate", "padd_view"}`. When `refining_centre_view` was added in the refinery layer, those nodes were wrongly tagged `physical` until the loader was patched. Deriving from `node_class` makes the logic future-proof: any new observational subtype gets tagged correctly without code changes.

**Example.** When `foreign_production_aggregate` (= `canadian_oil_sands`) was added, no loader change was needed — the node has `node_class = "production"` (it's a real production source, just foreign), so the loader correctly tags it `kind = "physical"`. If a future `oecd_consumption_view` is added with `node_class = "observational"`, it will automatically be tagged `kind = "abstract"`.

### B10. JSON is the canonical input; Postgres is the runtime

**Premise.** `asset_graph/asset_graph.json` is the authoritative source of structure. The Postgres `oil_network` schema is reproducible from the JSON via `load_asset_graph.ipynb`. Direct SQL writes to the structural tables are forbidden.

**Why.** Reproducibility. A new machine, a wiped database, a colleague spinning up — they can all rebuild from JSON. Direct SQL writes would create state that exists only in one Postgres instance and would be lost on the next rebuild.

**Example.** When a colleague at home pulls the repo and runs `initialize_oil_logistics_network.ipynb`, the schema is recreated from scratch and the JSON is UPSERTed in. Their database state matches the developer's bit-for-bit (modulo timeseries data, which lives separately).

### B11. Idempotent UPSERTs with patch scripts

**Premise.** All structural patches go through an `add_*.py` script that re-reads `asset_graph.json`, applies changes only if the new entries don't already exist (`if id not in existing_ids`), writes the JSON back, and refreshes the meta counts. Re-running any patch script is safe.

**Why.** Avoids duplicate state. If a patch script were not idempotent, re-running it would produce duplicate edges, doubled counts, or constraint violations. Idempotency means the workflow is forgiving — running the same script twice produces the same result as running it once.

**Example.** When `add_canadian_layer.py` is re-run, it iterates through its 5 new node IDs and 8 new edge IDs, checks whether each is already in the JSON, and only appends the missing ones. The second run prints "Added 0 nodes, 0 edges" but does no harm.

---

## C. Operational premises

### C1. `variable_assignments` binds to either timeseries OR formula (never both, never neither)

**Premise.** Each row in `variable_assignments` has both a `timeseries_id` column and a `formula` column, and a CHECK constraint enforces `num_nonnulls(timeseries_id, formula) = 1` — exactly one is populated.

**Why.** A variable's value comes from observation OR computation, not both. Allowing both would create ambiguity (which wins?); requiring neither would make the assignment meaningless.

**Example.** `consumption__crude__ref_motiva_port_arthur` for the starter scenario will be bound to a `timeseries_id` referencing `PET.MCRRIPP3.M` (or a per-facility series when one becomes available) — formula is NULL. By contrast `production__crude__padd3_production_view` is bound to `formula = "sum_over_children(production)"` — timeseries_id is NULL.

### C2. `node_type_default_formulas` as a fallback layer

**Premise.** If no explicit assignment exists for a `(node_type × variable_type)` pair, the default formula in `node_type_default_formulas` fires. This avoids writing one assignment per slot when the formula is the same for all instances of that type.

**Why.** Reduces duplication. Most pipelines compute inventory the same way (`prev(inventory) + sum_inflow - sum_outflow`); rather than writing that formula 20 times (once per pipeline), it lives once in the defaults table and applies to all pipeline-type nodes.

**Example.** All 24 pipelines in the graph (Gray Oak, Cactus II, Mainline, Keystone, etc.) share the same `inventory` formula. With defaults, that's one row in `node_type_default_formulas`: `(pipeline, inventory) → "prev(inventory) + sum_inflow - sum_outflow"`. Without defaults, you'd write 24 separate `variable_assignments` rows.

### C3. Foreign sources have explicit upstream nodes

**Premise.** Foreign crude entering the US through a pipeline or terminal is modelled with an explicit *foreign production source node* upstream of the entry point. The entry point itself is not treated as a free source.

**Why.** Without an explicit upstream, an import terminal (e.g., `clearbrook_entry`) acts as a node that produces crude from nothing — mass balance is ill-defined and the volume has no traceable origin. Adding a Canadian production node upstream makes the inflow capacity-bounded and source-attributed.

**Example.** Before the Canadian layer was added, `clearbrook_entry` had zero inbound edges and pumped out ~3 mb/d of Canadian Mainline crude with no traceable source. Now it has an inbound edge from `pipe_enbridge_mainline_ca`, which in turn is fed by `canadian_oil_sands` (a foreign production aggregate at Hardisty AB with `country = "CA"`, capacity 5,000 kbd). The Canadian crude reaching Whiting now traces all the way back to Alberta.

### C4. Per-PADD residuals close mass-balance gaps

**Premise.** When the modelled refining capacity in a PADD is less than the real US capacity, the gap is closed by a single residual refinery node tagged `is_residual = true`, fed from the same hubs that feed the named refineries in that PADD.

**Why.** Mass balance requires every barrel produced + imported to have a sink. Without residuals, ~12 mb/d of US refining capacity (the small + mid-size refineries we don't name individually) would be missing, and Cushing/Patoka/Houston would over-supply their nameplate refineries. Residuals make the macro balance close while preserving the named refineries' specificity.

**Example.** `ref_padd3_residual` has `capacity_bd = 6,700,000`, `is_residual = true`, and `examples = ["Phillips 66 Sweeny TX", "Valero Port Arthur TX", "ExxonMobil Beaumont TX", "Total Port Arthur TX", ...]`. It rolls up under `padd3_refining_view` alongside the 5 named PADD3 refineries. When per-facility data lands later, the residual can be split into named refineries (the `examples` list traces which).

### C5. Bidirectional `loop_terminal` modelled as both export and import

**Premise.** LOOP (Louisiana Offshore Oil Port) is modelled as a single node with `node_type = "export_terminal"` because the model needs it as a sink for export flows, but it has both inbound (foreign tanker imports + offshore Gulf production via LOCAP/LOOP-connector) and outbound (export tanker loadings + onshore re-routing to Garyville) edges.

**Why.** LOOP is the only US port that handles VLCCs in both directions. A purely export-terminal model would understate its role. Treating it as a hub-with-export-terminal-typing keeps the topology accurate.

**Example.** `loop_terminal` has 3 inbound edges (`gulf_of_america → offshore_outflow`, `pipe_locap → pipeline_outflow`, `pipe_loop_connector ↔ pipeline_outflow`) and 2 outbound edges (`pipe_loop_connector → st_james_hub`, `loop_terminal → ref_marathon_garyville`). The reverse view in the audit confirms: LOOP receives crude from US Gulf production AND foreign tanker imports, and sends crude to St. James + Garyville.

### C6. Collapsed sub-terminals expected to have no inbound under starter contract

**Premise.** Some physical assets (Cushing operator sub-terminals, Houston export sub-facilities) are in the asset graph but have `starter_status = "collapsed"`. Under the starter contract their flows are inherited from their parent node. Audit scripts annotate "<<no source — collapsed sub-terminal under the starter contract>>" rather than flagging them as bugs.

**Why.** A "no source reaches it" warning would be a false positive for these nodes. The starter scenario deliberately routes their flows through the parent. They will reactivate in finer-resolution scenarios.

**Example.** `seabrook_export`, `echo_export_dock`, `houston_ship_channel_export` are physical sub-docks within the Port of Houston; each is a real LP-9 terminal. Under the starter contract, all Houston exports route via `houston_export` (the aggregate). The three sub-docks have zero edges and `starter_status = "collapsed"`. The source-sink audit recognises this via the starter_status field and prints the explanatory note rather than a bug.

### C7. Capacity numbers live on attributes, not on the schema

**Premise.** Pipeline capacity (`capacity_bpd`), refinery capacity (`capacity_bd`), Nelson Complexity Index, slate, operator etc. all live in `assets.attributes.configuration` JSONB or in `variables.attributes` JSONB, not as first-class typed columns.

**Why.** Different node types need different attributes. A pipeline has diameter and length; a refinery has Nelson index and conversion units; a storage hub has tank-farm capacity. Adding typed columns for every possible attribute would bloat the schema with mostly-NULL columns. JSONB lets each node carry whatever metadata makes sense for its kind.

**Example.** `ref_motiva_port_arthur.attributes.configuration = { "operator": "Motiva (Saudi Aramco)", "capacity_bd": 626000, "nelson_complexity_index": 10.5, "has_fcc": true, "has_hydrocracker": false, "has_coker": true, "preferred_slate": "heavy_sour" }`. By contrast `pipe_keystone.configuration = { "operator": "South Bow", "diameter_in": 30, "length_miles": 2147, "capacity_bpd": 620000, "transit_days": 10, "commissioning_year": 2010 }`. Same JSONB column, different attribute schemas.

---

## Cross-references

- Framework principles in narrative form: [CLAUDE.md](CLAUDE.md) §2
- Brief restatement: [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)
- Detailed schema decisions: [oil_network_design.md](oil_network_design.md)
- Live state and resume instructions: [HANDOVER.md](HANDOVER.md)
- Audit scripts that exercise these premises: `node_audit.py`, `verify_routing_fixes.py`, `mass_balance_check.py`, `coverage_check.py`, `source_sink_audit.py`
