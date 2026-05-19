"""Generate `Graph_Construction.pdf` — how the asset graph and scenario
state are constructed: physical vs abstract nodes, the partition tree,
per-scenario variable_assignments, and the partition closure result.

Output: Graph_Construction.pdf
"""
from __future__ import annotations
from paths import DOCS_DIR

from pathlib import Path

from pdf_utils import (
    bullets, body, build_pdf, build_styles, code, cover, data_table,
    heading,
)
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Spacer

ROOT = Path(__file__).parent
OUT = DOCS_DIR / "Graph_Construction.pdf"


def main():
    styles = build_styles()
    flow = []

    # ---- Cover ------------------------------------------------------------
    flow += cover(
        title="Graph &amp; Scenario Construction",
        subtitle="How the asset graph is built &mdash; physical nodes, abstract aggregates, and the partition tree",
        doc_title="Graph Construction",
        version="1.0",
        styles=styles,
    )

    # ---- 1. Two-layer model -----------------------------------------------
    flow.append(heading("1. The two-layer model", 1, styles))
    flow.append(body(
        "The framework keeps two layers separated cleanly. The "
        "<b>persistent asset graph</b> is the fixed, superset physical topology "
        "&mdash; every node and every feasible edge defined once and only once. "
        "It does not change when data sources change. The <b>scenario state</b> "
        "is derived at load time from the persistent asset graph plus the "
        "per-scenario rows in <font face=\"Courier\">variable_assignments</font>; "
        "this is what downstream consumers query.",
        styles))
    flow.append(body(
        "Why two layers. The empirical data the framework consumes (EIA monthly "
        "series, for the U.S. crude case) covers different subsystems at different "
        "resolutions and changes vintage as new series are published. A representation "
        "that adapts to data shape by rewriting nodes and edges would force every "
        "consumer to keep up with that vintage. The two-layer model puts the "
        "volatility in <font face=\"Courier\">variable_assignments</font> instead: "
        "the asset graph stays stable; the scenario state regenerates when the "
        "assignment rows change. (Earlier framings of this idea introduced a "
        "&ldquo;coverage contract&rdquo; object as a separate primary structure. "
        "In the implementation, the contract is mechanically just the assignment "
        "table tagged with a scenario_id &mdash; no separate object exists.)",
        styles))
    flow.append(body(
        "Concretely, if per-refinery operating stocks become available, the "
        "asset graph does not change. The affected variables are re-pointed "
        "from PADD-level TS to refinery-level TS in "
        "<font face=\"Courier\">variable_assignments</font>; the listed refineries "
        "are inferred as <i>authoritative</i> by <font face=\"Courier\">"
        "v_node_status</font> (rather than the <i>collapsed</i> they were "
        "previously). The refineries already exist as nodes with inventory "
        "variables.",
        styles))
    flow.append(PageBreak())

    # ---- 2. The starter asset graph at a glance ---------------------------
    flow.append(heading("2. The starter asset graph", 1, styles))
    flow.append(body(
        "The starter asset graph (target: U.S. crude oil, January 2015 onwards) "
        "carries <b>240 assets</b>: 197 physical and 43 abstract aggregates "
        "(including 4 boundary nodes). They connect via 409 directed flow edges, "
        "and they carry 1,830 variables under the active scenario "
        "<font face=\"Courier\">starter_us_crude_2015_2025</font>.",
        styles))
    flow.append(body(
        "Every node has a <i>kind</i> (physical or abstract), a "
        "<i>node_class</i> (production, infrastructure, observational), a "
        "<i>node_subtype</i> (refinery, gathering, pipeline, region_view, "
        "&hellip;), and a <i>starter_status</i> (authoritative, collapsed, "
        "derived). Geographic metadata (lat/lon, state, county, PADD) is "
        "stored separately in <font face=\"Courier\">oil_network.locations</font> "
        "to keep labelling distinct from structure (Principle 2.7).",
        styles))
    flow.append(PageBreak())

    # ---- 3. Physical nodes ------------------------------------------------
    flow.append(heading("3. Physical nodes", 1, styles))
    flow.append(body(
        "Physical nodes represent real, named assets on the ground. They carry "
        "geographic coordinates, capacity, configuration, and flow edges. "
        "Adding a new physical node usually means a real operational change: "
        "a new refinery, a new pipeline, a new export terminal coming online. "
        "The starter graph has the following physical inventory:",
        styles))

    flow.append(data_table(
        ["Subtype", "Count", "Examples / role"],
        [
            ["state_sub_basin", "5", "bakken_nd, bakken_mt, permian_tx, permian_nm, eagle_ford_tx"],
            ["state_conventional", "7", "oklahoma_conventional, california_conventional, wyoming, &hellip;"],
            ["state_residual", "5", "padd1_other &hellip; padd5_other (covers the un-named tail)"],
            ["offshore_region", "1", "gulf_of_america"],
            ["gathering", "6", "ans, bakken_nd, bakken_mt, eagle_ford, permian_nm, permian_tx"],
            ["origin_terminal", "6", "midland, wink, crane, johnsons_corner, three_rivers, valdez"],
            ["storage_terminal", "10", "Cushing (hub + 3 sub-operators), Patoka, Houston, &hellip;"],
            ["spr_site", "4", "bayou_choctaw, big_hill, bryan_mound, west_hackberry"],
            ["refinery", "115", "19 named + 5 PADD residuals + sub-aggregates (24 active)"],
            ["import_terminal", "8", "clearbrook, 4 PADD imports_agg, 2 Canadian imports_agg, padd2_xstate&hellip;"],
            ["export_terminal", "12", "Ingleside, Houston-agg + 3 subs, Nederland, LOOP, 5 exports_agg"],
            ["pipeline", "37", "20 US-internal + 4 cross-border + 12 inter-PADD aggs + the new bakken xstate"],
            ["foreign_export_destination", "1", "boundary sink: where exports physically leave"],
            ["foreign_production_aggregate", "2", "canadian_oil_sands, foreign_supply"],
        ],
        styles, col_widths=(4 * cm, 1.5 * cm, 10.5 * cm),
    ))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(body(
        "A few of these node categories warrant comment.",
        styles))
    flow.append(body(
        "<b>State residuals</b> (padd1_other &hellip; padd5_other) are physical "
        "in the sense that they aggregate real but un-named refineries within "
        "the PADD. They exist because EIA publishes a PADD total that exceeds "
        "the sum of the named facilities, and the difference must be attributed "
        "somewhere. They will be replaced by named facilities as data granularity "
        "improves &mdash; each residual carries an explicit list of the "
        "refineries it stands in for, so the replacement is traceable.",
        styles))
    flow.append(body(
        "<b>Inter-PADD and import/export aggregates</b> (added in the tenth "
        "pass) are physical nodes in the technical sense &mdash; they have "
        "<font face=\"Courier\">kind = 'physical'</font>, an asset row, and "
        "concrete flow edges &mdash; but they aggregate over groups of real "
        "pipelines and terminals rather than naming a single facility. They "
        "exist to give the partition tree a same-type child to point at, and "
        "they are the natural home for future per-grade or per-operator "
        "decomposition.",
        styles))
    flow.append(body(
        "<b>The Bakken cross-state connector</b> "
        "(<font face=\"Courier\">pipe_bakken_xstate</font>, added in the "
        "eleventh pass) is a bidirectional pipeline-type node connecting "
        "<font face=\"Courier\">bakken_nd_gathering</font> and "
        "<font face=\"Courier\">bakken_mt_gathering</font>. It models the real "
        "midstream pooling between the two state halves of the Bakken (operated "
        "by Hess, Crestwood, Energy Transfer). All four of its flow variables "
        "are latent in the starter scenario, because no public TS measures "
        "inter-state Bakken gathering flow; the constraint &Sigma;I = &Sigma;O "
        "at the connector follows from the pipeline node-type defaults.",
        styles))
    flow.append(PageBreak())

    # ---- 4. Abstract nodes ------------------------------------------------
    flow.append(heading("4. Abstract nodes (observational aggregates)", 1, styles))
    flow.append(body(
        "Abstract nodes do not represent physical assets. They are aggregation "
        "views over physical nodes &mdash; the substrate for partition rollups, "
        "data attribution at coarser scales than the physical layer, and "
        "Chapter-5 consistency claims. They have <font face=\"Courier\">kind = "
        "'abstract'</font> and no flow edges of their own; their relationships "
        "to physical nodes are through <font face=\"Courier\">formula_inputs</font>.",
        styles))
    flow.append(data_table(
        ["Subtype", "Count", "Role"],
        [
            ["region_view", "6", "<font face=\"Courier\">usa_view</font>, "
                                 "<font face=\"Courier\">padd1_view</font> &hellip; "
                                 "<font face=\"Courier\">padd5_view</font>. "
                                 "The geographic partition spine."],
            ["refining_district_view", "10", "EIA refining-district rollups (R3A, R3B, &hellip;)."],
            ["state_view", "2", "texas_state_view, montana_state_view &mdash; "
                                "rollups by state used for sub-PADD cross-checks."],
            ["observational_aggregate", "4", "spr_total, basin aggregates "
                                              "(permian, bakken, eagle_ford) when used as constraint rollups."],
            ["usa_subtotal_view", "1", "usa_lower48_excl_gom_view &mdash; STEO subtotal."],
        ],
        styles, col_widths=(5 * cm, 1.5 * cm, 9.5 * cm),
    ))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(body(
        "<b>Why region_view nodes are abstract.</b> A PADD is an administrative "
        "label, not a physical asset. There is no piece of equipment one can "
        "point at and call &ldquo;PADD 2&rdquo;. EIA reports aggregated data at "
        "this scale, and the framework needs a node that can carry that "
        "aggregated data, but it is structurally different from a refinery or "
        "a pipeline. Treating it as physical would invite confusion at the edges "
        "(does PADD 2 have an outflow to PADD 3, or is that outflow the sum of "
        "the underlying pipelines?). The framework picks the latter: PADD-view "
        "outflow is alias-derived from the corresponding inter-PADD aggregate&rsquo;s "
        "outflow, never TS-bound directly.",
        styles))
    flow.append(body(
        "<b>Why refining_district_view nodes are abstract.</b> EIA refining "
        "districts (PADD 3A, 3B, 3C, &hellip;) group refineries for reporting; "
        "they are not co-owned by a single operator, do not share custody "
        "transfer mechanics, and do not have a single physical address. They "
        "exist to host the district-level consumption series that EIA publishes "
        "and to enable the sum-over-children identity that PADD-level consumption "
        "must equal the sum of district consumptions.",
        styles))
    flow.append(PageBreak())

    # ---- 5. Boundary nodes ------------------------------------------------
    flow.append(heading("5. Boundary nodes", 1, styles))
    flow.append(body(
        "Four nodes sit on the boundary of the modelled system. They are "
        "physical in the sense that they represent real entities or aggregates, "
        "but they are not full participants in the partition.",
        styles))
    flow.append(data_table(
        ["Node", "Subtype", "Role"],
        [
            ["foreign_supply", "foreign_production_aggregate",
             "All non-Canadian foreign crude flowing into U.S. import terminals. "
             "Boundary source; foreign-side mass balance is not modelled."],
            ["canadian_oil_sands", "foreign_production_aggregate",
             "All Canadian crude entering U.S. via Mainline, Keystone, "
             "Express+Platte, TMX. Boundary source."],
            ["foreign_export_destination", "foreign_export_destination",
             "Boundary sink. All U.S. crude exports flow into this node; "
             "we do not model where they go from here."],
            ["spr_total", "observational_aggregate",
             "Sum of the four SPR sites. Constraint node, used for cross-checks "
             "rather than as a partition member."],
        ],
        styles, col_widths=(5 * cm, 5 * cm, 6 * cm),
    ))
    flow.append(PageBreak())

    # ---- 6. Variables ------------------------------------------------------
    flow.append(heading("6. Variables: the authoritative data model", 1, styles))
    flow.append(body(
        "Variables are the unit of data attribution. Every node carries one "
        "variable per (variable_type, commodity, related_node) tuple. The seven "
        "variable types are:",
        styles))
    flow.append(data_table(
        ["Type", "Symbol", "Description", "Relational?"],
        [
            ["production", "P", "Crude produced at this node.", "no"],
            ["consumption", "C", "Crude consumed at this node (refinery throughput).", "no"],
            ["inventory", "S", "Crude stocks held at this node.", "no"],
            ["balancing_item", "B", "Reporting residual; absorbs systematic discrepancies.", "no"],
            ["inflow", "F<sub>in</sub>", "Crude flowing in from a specific related_node.", "yes"],
            ["outflow", "F<sub>out</sub>", "Crude flowing out to a specific related_node.", "yes"],
            ["phi", "&phi;", "Reference variable (used for cross-node alias chains).", "yes"],
        ],
        styles, col_widths=(3 * cm, 1.5 * cm, 9 * cm, 2.5 * cm),
    ))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(body(
        "Each variable carries either a <i>timeseries_id</i> (TS-bound, "
        "observed) or a <i>formula</i> (derived), never both &mdash; enforced "
        "by the schema-level CHECK constraint <font face=\"Courier\">"
        "num_nonnulls(timeseries_id, formula) = 1</font>. This is the "
        "schema-level expression of Principle 2.8.",
        styles))
    flow.append(body(
        "The variables collection is the authoritative data model. Every other "
        "view of the graph &mdash; the edge list, the partition tree, the "
        "resolution hierarchy &mdash; is derived from it. There is no second "
        "source of truth to keep in sync.",
        styles))
    flow.append(PageBreak())

    # ---- 7. Partition tree -----------------------------------------------
    flow.append(heading("7. The partition tree", 1, styles))
    flow.append(body(
        "The partition tree is the same-type aggregation hierarchy that drives "
        "the Chapter-5 consistency claim. A node M is a partition child of "
        "node N if some variable on N has <font face=\"Courier\">formula_inputs"
        "</font> referencing a same-type, same-commodity, same-related-node "
        "variable on M.",
        styles))
    flow.append(body(
        "The <i>same-type</i> qualifier is critical. Aggregation links "
        "(<font face=\"Courier\">padd_view.P = &Sigma; basin.P</font>) connect "
        "variables of the same type. Cross-edge aliases "
        "(<font face=\"Courier\">padd_view.inflow = alias(other_padd.outflow)</font>) "
        "connect variables of <i>different</i> types &mdash; these are inter-PADD "
        "references, not partition relations. Filtering on type cleanly "
        "distinguishes the two.",
        styles))
    flow.append(body(
        "The starter scenario carries 265 partition edges across 28 distinct "
        "parents and 197 distinct children. The active partition spine runs:",
        styles))
    flow.append(code(
        "usa_view\n"
        "├── padd1_view\n"
        "│   ├── refining districts (RAP, REC)\n"
        "│   ├── padd1_imports_agg          ← MCRIPP12 − MCRIPP1CA2\n"
        "│   ├── padd1_canadian_imports_agg ← MCRIPP1CA2\n"
        "│   ├── padd1_exports_agg          ← MCREXP12\n"
        "│   ├── inter_padd_1_to_2_agg, inter_padd_1_to_3_agg\n"
        "│   └── padd1_other (residual)\n"
        "├── padd2_view\n"
        "│   ├── refining districts (R2A, R2B, R2C)\n"
        "│   ├── bakken_nd, oklahoma, padd2_other\n"
        "│   ├── cushing_hub (+ 3 operator sub-terminals), patoka_hub\n"
        "│   ├── bakken_nd_gathering   (inventory-child, post-11th pass)\n"
        "│   ├── padd2_canadian_imports_agg ← MCRIPP2CA2\n"
        "│   ├── padd2_exports_agg          ← MCREXP22\n"
        "│   └── inter_padd_2_to_{1,3,4}_agg  + receiver-side aliases\n"
        "├── padd3_view, padd4_view, padd5_view  (analogous)\n"
        "│\n"
        "└── boundary nodes (sit outside the partition):\n"
        "    foreign_supply, canadian_oil_sands, foreign_export_destination, spr_total",
        styles))
    flow.append(body(
        "Partition closure is the algebraic identity that the audit verifies: "
        "for every parent node N and every variable_type T, the own value of "
        "N&rsquo;s T variable equals the sum of N&rsquo;s partition children&rsquo;s T "
        "variables. This holds (within rounding) across all PADDs and at "
        "usa_view in the current state.",
        styles))
    flow.append(PageBreak())

    # ---- 8. Scenarios and authoritative declarations ---------------------
    flow.append(heading("8. Scenarios and authoritative declarations", 1, styles))
    flow.append(body(
        "A scenario is a pair: the persistent asset graph plus a set of "
        "<font face=\"Courier\">variable_assignments</font> rows tagged with "
        "the scenario_id. Different scenarios share the asset graph and the "
        "TS data; they differ in their assignment rows.",
        styles))
    flow.append(body(
        "The schema-level CHECK <font face=\"Courier\">num_nonnulls(timeseries_id, "
        "formula) = 1</font> means each assignment row either binds the variable "
        "to a TS (authoritative) or to a formula (derived) &mdash; never both, "
        "never neither. The set of assignments under a scenario therefore "
        "encodes, for every variable, exactly which level is authoritative and "
        "which is derived. There is no separate &ldquo;coverage contract&rdquo; "
        "structure; the per-variable choices are the contract.",
        styles))
    flow.append(body(
        "The starter scenario&rsquo;s authoritative declarations summarise as:",
        styles))
    flow.append(data_table(
        ["Subsystem", "Authoritative level", "Reason"],
        [
            ["Permian, Bakken", "basin aggregate", "EIA publishes only at basin level"],
            ["Eagle Ford", "state-basin", "Single state, equivalent to basin"],
            ["Gulf of America, Alaska", "physical node", "Reported separately by EIA"],
            ["Rest of L48", "residual aggregate", "EIA publishes only the residual"],
            ["SPR", "site level", "EIA PSM Table 38 publishes per site monthly"],
            ["Cushing", "hub level", "Operator sub-terminals collapsed; storage report at hub"],
            ["Houston export", "aggregate", "Facility sub-terminals collapsed"],
            ["Stocks", "PADD level (auxiliary at sub-)", "MCRSFP+MCRRSP at PADD; sub-decomposition would double-count"],
            ["B (balancing item)", "PADD + USA", "MCRUA series, ninth-pass promotion to TS-observed"],
            ["Inter-PADD flows", "PADD-aggregate level", "MCRMP_*_*_* at the combined aggregate, per-pipe latent"],
        ],
        styles, col_widths=(4 * cm, 4 * cm, 8 * cm),
    ))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(body(
        "Cross-scenario consistency (the Chapter-5 contribution) tests that "
        "the same TS data, evaluated under progressively finer assignment sets, "
        "produces consistent aggregate values. The current implementation has "
        "<font face=\"Courier\">starter_us_crude_2015_2025</font> as the only "
        "scenario; <font face=\"Courier\">starter_basin</font> (a coarser "
        "contract) is queued for the next pass.",
        styles))
    flow.append(PageBreak())

    # ---- 9. The current closure state ------------------------------------
    # ---- 10. End-to-end pipeline ------------------------------------------
    flow.append(heading("10. End-to-end pipeline: load &rarr; resolve &rarr; render", 1, styles))
    flow.append(body(
        "Every run of the framework follows three phases. This section walks "
        "through each, naming the script and the tables it writes.",
        styles))

    flow.append(heading("10.1 Load the database (one-time + ad-hoc migrations)", 2, styles))
    flow.append(body(
        "Phase 1 populates the persistent asset graph and per-scenario "
        "<font face=\"Courier\">variable_assignments</font>. The seed file "
        "<font face=\"Courier\">clean/config/asset_graph.json</font> is the "
        "starting point; migration scripts incrementally evolve the graph "
        "(adding aggregates, splitting nodes, fixing routes) by writing "
        "directly to the base tables.",
        styles))
    flow.append(data_table(
        ["Step", "Script", "Writes to"],
        [
            ["1.1", "<font face=\"Courier\">load_asset_graph.ipynb</font>",
             "<font face=\"Courier\">assets, nodes, locations, variables</font> &mdash; the persistent asset graph from JSON"],
            ["1.2", "<font face=\"Courier\">assign_eia.ipynb</font>",
             "<font face=\"Courier\">timeseries, timeseries_data</font> &mdash; EIA series catalogue + monthly facts"],
            ["1.3", "<font face=\"Courier\">assign_formulas.ipynb</font>",
             "<font face=\"Courier\">variable_assignments</font> &mdash; per-scenario TS or formula recipe per variable"],
            ["1.4", "<font face=\"Courier\">populate_node_type_defaults.py</font>",
             "<font face=\"Courier\">node_type_default_formulas</font> &mdash; default recipes per node type"],
            ["1.5", "Migration scripts (<font face=\"Courier\">add_*.py</font>, <font face=\"Courier\">split_*.py</font>, <font face=\"Courier\">apply_*.py</font>, <font face=\"Courier\">repoint_*.py</font>, <font face=\"Courier\">*pass_*.py</font>)",
             "<font face=\"Courier\">assets / nodes / variables / variable_assignments</font> &mdash; topology + assignment evolutions"],
            ["1.6", "<font face=\"Courier\">refresh_views.py --structural</font>",
             "Refreshes L2 + L3 materialised views (<font face=\"Courier\">v_formula_input_links, v_aggregation_edges, v_flow_edges, v_partition_tree, v_node_status</font>) after any change to <font face=\"Courier\">variable_assignments</font>"],
        ],
        styles, col_widths=(1 * cm, 6 * cm, 9 * cm),
    ))
    flow.append(Spacer(1, 0.3 * cm))
    flow.append(body(
        "The base tables under <font face=\"Courier\">oil_network</font> after "
        "phase 1: <font face=\"Courier\">assets, nodes, locations, variables, "
        "variable_assignments, node_type_default_formulas, scenarios, "
        "scenario_node_role, timeseries, timeseries_data, commodities, "
        "variable_types, node_types, graphs</font>. Plus the materialised views "
        "<font face=\"Courier\">v_effective_assignments</font> (L1, regular), "
        "L2 / L3 mat views listed above.",
        styles))
    flow.append(PageBreak())

    flow.append(heading("10.2 Use the solver (every assignment change)", 2, styles))
    flow.append(body(
        "Phase 2 evaluates every variable at every observation date, applying "
        "the dispatch rules described in <i>Resolver Walkthrough</i>.",
        styles))
    flow.append(data_table(
        ["Step", "Script", "Writes to"],
        [
            ["2.1", "<font face=\"Courier\">resolve_scenario.py</font>",
             "<font face=\"Courier\">scenario_resolver_runs</font> &mdash; one audit row per invocation (started_at, completed_at, duration_ms, n_assignments, n_rows_written, dispatch_stats as JSONB, free-text notes)"],
            ["2.2", "(same script)",
             "<font face=\"Courier\">scenario_resolved_values</font> &mdash; one row per (scenario, variable, observation_date) with value / source / formula_used / timeseries_id / run_id"],
            ["2.3", "(automatic, end of resolve_scenario)",
             "Calls <font face=\"Courier\">refresh_views.refresh_analytic()</font> &rarr; rebuilds L4 mat views (<font face=\"Courier\">v_node_balance_check, v_aggregation_consistency, v_inventory_changes, v_aggregate_balance, v_node_pcisob</font>)"],
        ],
        styles, col_widths=(1 * cm, 6 * cm, 9 * cm),
    ))
    flow.append(Spacer(1, 0.3 * cm))
    flow.append(body(
        "Audit scripts that read post-resolve state: "
        "<font face=\"Courier\">audit_partition_gaps.py</font> (gap.I/gap.O closure check), "
        "<font face=\"Courier\">audit_ts_binding_uniqueness.py</font> (1-TS-1-variable invariant), "
        "<font face=\"Courier\">verify_balance_cells.py</font> (cell-by-cell P/C/I/O/B/S sums).",
        styles))
    flow.append(PageBreak())

    flow.append(heading("10.3 Show the data on HTMLs", 2, styles))
    flow.append(body(
        "Phase 3 generates the visualisation HTMLs. Every renderer goes through "
        "<font face=\"Courier\">NetworkGraph</font> (the in-memory engine) which "
        "loads from the L2 / L3 / L4 mat views; no renderer issues raw SQL "
        "against base tables. Each HTML carries a metadata beacon "
        "(<font face=\"Courier\">oilnet-artefact</font>) naming the resolver "
        "run that produced it, and an audit row is written per regeneration.",
        styles))
    flow.append(data_table(
        ["Step", "Script", "Output / writes to"],
        [
            ["3.1", "<font face=\"Courier\">regenerate_htmls.py</font>",
             "Orchestrator: scans <font face=\"Courier\">clean/outputs/html/</font>, compares each HTML&rsquo;s beacon against the latest <font face=\"Courier\">scenario_resolver_runs.run_id</font>, calls only stale renderers (or all with <font face=\"Courier\">--force</font>)"],
            ["3.2", "<font face=\"Courier\">make_balance_resolver_ui.py</font>",
             "<font face=\"Courier\">oil_network_balance_resolver.html</font> &mdash; balance equation per node, drill-down by partition tree"],
            ["3.3", "<font face=\"Courier\">make_hierarchy_resolver_ui.py</font>",
             "<font face=\"Courier\">oil_network_hierarchy_resolver.html</font> &mdash; full asset-graph tree with variable inspector"],
            ["3.4", "<font face=\"Courier\">make_partition_map.py</font>",
             "<font face=\"Courier\">oil_network_partition_map.html</font> &mdash; click-to-drill geographic map of the partition tree"],
            ["3.5", "<font face=\"Courier\">make_node_neighbors_map.py</font>",
             "<font face=\"Courier\">oil_network_node_neighbors.html</font> &mdash; single-node flow-edge explorer"],
            ["3.6", "<font face=\"Courier\">make_map_resolver_ui.py</font>",
             "<font face=\"Courier\">oil_network_map_resolver.html</font> &mdash; flat geographic map of all physical assets + edges"],
            ["3.7", "Each renderer calls <font face=\"Courier\">render_utils.write_html()</font>",
             "<font face=\"Courier\">scenario_html_artefacts</font> &mdash; audit row per generation (scenario_id, run_id, view_name, file_path, file_size_bytes, generated_at, notes)"],
        ],
        styles, col_widths=(1 * cm, 6 * cm, 9 * cm),
    ))
    flow.append(Spacer(1, 0.3 * cm))
    flow.append(body(
        "After step 3.1 a single SQL query answers <i>which run produced which "
        "HTML</i>: <font face=\"Courier\">SELECT DISTINCT ON (view_name) "
        "view_name, run_id, generated_at FROM scenario_html_artefacts ORDER BY "
        "view_name, generated_at DESC</font>.",
        styles))
    flow.append(PageBreak())

    flow.append(heading("11. Where the framework stands today", 1, styles))
    flow.append(body(
        "After twelve passes of refinement, the partition audit reports clean "
        "closure on both sides for every PADD view and for usa_view:",
        styles))
    flow.append(data_table(
        ["Node", "own.I", "bdy.I", "gap.I", "own.O", "bdy.O", "gap.O"],
        [
            ["usa_view",   "6,557",   "6,557",   "0",   "3,752", "3,752", "0"],
            ["padd1_view", "705",     "705",     "0",   "92.2",  "92.2",  "0"],
            ["padd2_view", "4,503",   "4,503",   "0",   "2,426", "2,426", "0"],
            ["padd3_view", "3,631",   "3,631",   "0",   "4,264", "4,264", "0"],
            ["padd4_view", "552.7",   "552.7",   "0",   "987.5", "987.5", "0"],
            ["padd5_view", "1,183",   "1,183",   "0",   "&mdash;", "0",   "0"],
        ],
        styles, col_widths=(3 * cm, 2 * cm, 2 * cm, 1.5 * cm, 2 * cm, 2 * cm, 1.5 * cm),
    ))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(body(
        "TS-binding uniqueness is clean (80 distinct TS in 80 bindings, 1&colon;1 "
        "attribution per Principle 2.8). Zero unresolved variables across "
        "1,830 assignments &times; 156 monthly observations = 285,480 (variable, "
        "date) pairs. The framework&rsquo;s partition + Principle-2.11 latent-"
        "allocation machinery is end-to-end consistent.",
        styles))
    flow.append(Spacer(1, 0.3 * cm))
    flow.append(body(
        "This concludes the framework&rsquo;s representational contribution. The "
        "remaining work for the thesis lies in the Chapter-5 validation "
        "experiments (cross-scenario consistency, the observed-vs-closure-B "
        "view, the named-event Chapter 5 Claim 4 chart) and the case-study "
        "narrative in Chapter 6.",
        styles))

    # ---- Build ------------------------------------------------------------
    p = build_pdf(OUT, flow,
                  header_text="Graph &amp; Scenario Construction &mdash; Pedro Porfirio, NOVA IMS")
    print(f"Wrote {p}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
