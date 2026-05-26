"""Generate `Resolver_Walkthrough.pdf` &mdash; a step-by-step explanation of
how `resolve_scenario.py` walks the variable DAG and persists resolved values.

Output: Resolver_Walkthrough.pdf
"""
from __future__ import annotations
from paths import DOCS_DIR

from pathlib import Path

import psycopg2

from pdf_utils import (
    bullets, body, build_pdf, build_styles, code, cover, data_table,
    heading,
)
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Spacer

from resolve_scenario import DB

ROOT = Path(__file__).parent
OUT = DOCS_DIR / "Resolver_Walkthrough.pdf"

SCENARIO = "starter_us_crude_2015_2025"


def fetch_latest_dispatch():
    """Return (run_id, completed_at, n_rows, dispatch_dict) for the latest
    resolver run on the starter scenario. Used to keep Section 10 counts
    in sync with the live state."""
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT run_id, completed_at, n_rows_written, dispatch_stats
            FROM oil_network.scenario_resolver_runs
            WHERE scenario_id = %s AND completed_at IS NOT NULL
            ORDER BY run_id DESC LIMIT 1
        """, (SCENARIO,))
        row = cur.fetchone()
    return row if row else (None, None, None, {})


def main():
    styles = build_styles()
    flow = []

    # ---- Cover ------------------------------------------------------------
    flow += cover(
        title="Scenario Resolver",
        subtitle="A guided reading of <font face=\"Courier\">resolve_scenario.py</font>",
        doc_title="Resolver Walkthrough",
        version="1.0",
        styles=styles,
    )

    # ---- Section 1: Why this exists ---------------------------------------
    flow.append(heading("1. Why the resolver exists", 1, styles))
    flow.append(body(
        "Before the resolver, every consumer that wanted to read a variable&rsquo;s "
        "value had to re-implement resolution logic itself. The balance UI "
        "carried a 100-line CTE handling TS bindings, mirror formulas, "
        "reverse-paired-edge inheritance, and closure-equation evaluation. "
        "JavaScript walked arithmetic formulas at render time, because SQL "
        "could not evaluate multi-term expressions of the form "
        "<font face=\"Courier\">A &minus; B &minus; C</font>. Each new generator "
        "(hierarchy explorer, geographic map, Chapter-5 validation queries, "
        "future forecasting features) would have duplicated that work.",
        styles))
    flow.append(body(
        "The resolver collapses every consumer&rsquo;s resolution logic into a single "
        "deterministic pass:", styles))
    flow.append(code(
        "inputs   :  variables  +  variable_assignments  +  timeseries_data\n"
        "             +  the active scenario\n\n"
        "output   :  one row per (variable, date) in scenario_resolved_values",
        styles))
    flow.append(body(
        "Every downstream consumer becomes a thin <font face=\"Courier\">SELECT</font> "
        "over the result table. The resolver runs in roughly twenty seconds for the "
        "starter scenario (1,830 variables &times; 156 monthly observations); it is run "
        "once per assignment change, not once per consumer.",
        styles))
    flow.append(PageBreak())

    # ---- Section 2: The output table --------------------------------------
    flow.append(heading("2. The output table", 1, styles))
    flow.append(body(
        "The resolver writes to <font face=\"Courier\">oil_network."
        "scenario_resolved_values</font>. Two columns from the DDL deserve "
        "particular attention:", styles))
    flow.append(code(
        "CREATE TABLE oil_network.scenario_resolved_values (\n"
        "    scenario_id       TEXT NOT NULL  REFERENCES scenarios     ON DELETE CASCADE,\n"
        "    variable_id       TEXT NOT NULL  REFERENCES variables     ON DELETE CASCADE,\n"
        "    observation_date  DATE NOT NULL,\n"
        "    value             DOUBLE PRECISION,\n"
        "    source            TEXT NOT NULL CHECK (source IN\n"
        "                        ('observed', 'derived', 'zero', 'latent', 'unresolved')),\n"
        "    formula_used      TEXT,\n"
        "    timeseries_id     TEXT,\n"
        "    saved_date        TIMESTAMPTZ DEFAULT NOW(),\n"
        "    run_id            BIGINT REFERENCES scenario_resolver_runs,\n"
        "    PRIMARY KEY (scenario_id, variable_id, observation_date)\n"
        ");",
        styles))
    flow.append(body(
        "<b>The <font face=\"Courier\">value</font> column is nullable.</b> A NULL "
        "value paired with <font face=\"Courier\">source = 'latent'</font> is a "
        "valid and intentional row &mdash; it tells downstream consumers &ldquo;this "
        "variable is declared but its value is unknown by design; do not retry.&rdquo; "
        "This is the schema-level expression of Principle 2.11 (latent allocation "
        "at junctions): the resolver records that a flow exists structurally and "
        "is constrained by mass balance, but its per-edge value is not pinned "
        "down by observation.",
        styles))
    flow.append(body(
        "<b>The <font face=\"Courier\">source</font> column is a CHECK-constrained "
        "enum with five categories:</b>",
        styles))
    flow.append(data_table(
        ["source", "meaning"],
        [
            ["observed", "Direct time-series lookup &mdash; ground-truth measurement."],
            ["derived", "Value computed by a formula (alias, sum, closure, arithmetic, mirror)."],
            ["zero", "Structural zero (e.g. a refinery does not produce crude)."],
            ["latent", "Declared variable with no resolution path (value = NULL by design)."],
            ["unresolved", "A formula was given but could not be evaluated. Zero in a healthy run."],
        ],
        styles, col_widths=(3 * cm, 13 * cm),
    ))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(body(
        "<font face=\"Courier\">formula_used</font> preserves which resolution rule "
        "fired (the dispatch labels in Section 4). This is for auditing: it lets a "
        "reviewer reconstruct, for any value, the exact path that produced it.",
        styles))
    flow.append(body(
        "Every run also writes an audit row to <font face=\"Courier\">scenario_resolver_runs</font> "
        "with a started/completed timestamp, duration, dispatch counts as JSONB, "
        "and optional free-text notes. Every resolved-value row carries the "
        "<font face=\"Courier\">run_id</font> that produced it, so historical "
        "comparison is a single SQL join.",
        styles))
    flow.append(PageBreak())

    # ---- Section 3: End-to-end flow ---------------------------------------
    flow.append(heading("3. How a run flows, end to end", 1, styles))
    flow.append(body(
        "The top-level shape of <font face=\"Courier\">resolve()</font> follows five steps:",
        styles))
    flow.append(code(
        "                load()             build_deps()         topo sort\n"
        "DB tables   ─────────────►  assignments   ──────────►  deps dict  ──────►\n"
        "                                                                          │\n"
        "                                                                          ▼\n"
        "                                                              dispatch loop\n"
        "                                                              (rules 1..11)\n"
        "                                                                          │\n"
        "                                                                          ▼\n"
        "                                                              persist to\n"
        "                                                              scenario_resolved_values",
        styles))
    flow.append(body(
        "Each phase has one purpose, isolated from the others. The dispatch loop "
        "never reads from the DB; the loader never evaluates formulas; the "
        "topological sort never produces values. Mistakes in one phase are "
        "diagnosed by inspecting its single concern.",
        styles))
    flow.append(PageBreak())

    # ---- Section 3.5: Where the resolver starts ---------------------------
    flow.append(heading("3.5  Where the resolver starts", 1, styles))
    flow.append(body(
        "A common framing of the resolver is that it starts from &ldquo;production "
        "nodes with <i>P</i> set and outflow equal to production.&rdquo; That is "
        "a special case, not the rule. The actual starting points are the "
        "<b>leaves of the dependency DAG</b> &mdash; every variable with no "
        "upstream dependency. There are two categories:",
        styles))
    flow.append(body(
        "<b>(a) Observed variables.</b> Any variable with "
        "<font face=\"Courier\">timeseries_id IS NOT NULL</font>. The TS lookup is "
        "the ground truth; no other rule contributes to its value. Examples: "
        "<font face=\"Courier\">production__crude__bakken_nd</font> (EIA series "
        "<font face=\"Courier\">COPRPM</font>), "
        "<font face=\"Courier\">consumption__crude__padd3_view</font> (refinery "
        "inputs).",
        styles))
    flow.append(body(
        "<b>(b) Structural zeros.</b> Any variable with "
        "<font face=\"Courier\">formula = '0'</font>. These come from "
        "<font face=\"Courier\">node_type_default_formulas</font> &mdash; a "
        "pipeline&rsquo;s <i>P</i>, <i>C</i>, <i>&Delta;S</i>, and <i>B</i> are zero "
        "by physical construction; a refinery&rsquo;s outflow is zero (refineries "
        "consume crude, they do not return it). No upstream variable contributes "
        "to a structural zero either.",
        styles))
    flow.append(body(
        "Everything else is derived from these two via the topologically "
        "ordered dispatch loop. The propagation works bottom-up: an alias "
        "needs its target resolved first; a sum needs its inputs; a closure "
        "needs the inventory delta and the flows. The topological sort "
        "guarantees each variable is visited only after its dependencies are.",
        styles))
    flow.append(body(
        "For a basin production node like <font face=\"Courier\">bakken_nd</font>, "
        "the chain is: <i>P</i> observed &rarr; <i>C</i>, <i>&Delta;S</i>, <i>B</i> = 0 "
        "(structural defaults) &rarr; <i>O</i> alias-bound to <i>P</i> (single-outflow "
        "fanout). That is the original framing&rsquo;s &ldquo;O = P&rdquo;, but the "
        "rule that fires is rule 5 (alias), and the special-case framing breaks "
        "for multi-outflow nodes (rule 5 cannot fire; <i>O</i> stays latent).",
        styles))
    flow.append(PageBreak())

    # ---- Section 3.6: A common misconception ------------------------------
    flow.append(heading("3.6  A common misconception: \"o1 = i2 + i3\"", 1, styles))
    flow.append(body(
        "The framework does <b>not</b> automatically aggregate the outflow of "
        "one node from the inflows of its downstream neighbours, even when "
        "the routing topology would seem to imply it. Each directed flow is "
        "its own variable. The pair "
        "<font face=\"Courier\">outflow__crude__permian_tx__permian_tx_gathering</font> "
        "and <font face=\"Courier\">inflow__crude__permian_tx_gathering__permian_tx</font> "
        "are <i>two different variables</i> that happen to refer to the same "
        "physical edge. They are linked by the <b>reverse-mirror rule</b> "
        "(Rule 6) &mdash; if one is observed or derived, the other inherits "
        "from it &mdash; not by summation.",
        styles))
    flow.append(body(
        "The &ldquo;<i>o</i>1 = <i>i</i>2 + <i>i</i>3&rdquo; pattern is "
        "different: it is a <i>node-level mass balance constraint</i>, not a "
        "sum-of-variables formula. At a gathering node, the resolver does "
        "not derive any one outflow from the sum of inflows. What does the "
        "framework offer instead?",
        styles))
    flow.extend(bullets([
        "<b>v_node_balance_check</b> (L4a, refreshed per resolver run) "
        "exposes <font face=\"Courier\">sum_in</font>, "
        "<font face=\"Courier\">sum_out_obs</font>, "
        "<font face=\"Courier\">sum_out_implied</font>, and "
        "<font face=\"Courier\">gap_kbd</font> for every (node, date). "
        "The mass-balance constraint is a queryable artefact, not a "
        "propagating formula.",
        "When the per-edge split is not observable in public data (e.g. "
        "the three Permian-to-Gulf outflow routes), each edge stays "
        "<i>latent</i>. The aggregate constraint &Sigma;<i>O</i> = "
        "&Sigma;<i>I</i> still holds in v_node_balance_check; the LP "
        "exporter (future work) reads the latents as decision variables "
        "and the constraint as an equality row.",
        "Aggregation in the other direction &mdash; "
        "<i>parent = &Sigma; constituents</i> &mdash; <i>is</i> a "
        "propagating formula: declared at construction time via "
        "<font face=\"Courier\">formula_inputs</font> on the parent's "
        "assignment, then applied by rule 4 (sum) at resolution time. "
        "Aggregation parent-to-children, not edge-to-edge.",
    ], styles))
    flow.append(PageBreak())

    # ---- Section 3.7: latent vs unresolved --------------------------------
    flow.append(heading("3.7  Latent vs unresolved", 1, styles))
    flow.append(body(
        "Both leave <font face=\"Courier\">value = NULL</font>, but they mean "
        "different things and the framework treats them differently. The "
        "distinction matters for downstream consumers and especially for "
        "the future LP / optimisation layer.",
        styles))
    flow.append(data_table(
        ["source", "Meaning", "How a downstream consumer should treat it"],
        [
            ["<font face=\"Courier\">latent</font>",
             "Declared variable, no resolution path. The assignment is "
             "<font face=\"Courier\">formula = 'latent()'</font> and no "
             "reverse-mirror promotion fired. Value is unknown by design.",
             "Free variable in an optimisation; constrained by mass balance, "
             "capacity, and observations that reference it. Typical use: "
             "per-route flow at a fan-out junction (Principle 11)."],
            ["<font face=\"Courier\">unresolved</font>",
             "A formula was given but the resolver could not evaluate it. "
             "Either a referenced variable is missing or the formula pattern "
             "is unmodelled. <i>Zero rows expected in a healthy run.</i>",
             "A bug. Fix the assignment (correct the reference) or extend "
             "the resolver (add a rule). Never silently treated as latent."],
        ],
        styles, col_widths=(2.6 * cm, 6.5 * cm, 6.9 * cm),
    ))
    flow.append(PageBreak())

    # ---- Section 4: The ten resolution rules ------------------------------
    flow.append(heading("4. The dispatch loop: ten resolution rules", 1, styles))
    flow.append(body(
        "Rules are tried in priority order. The first rule that succeeds "
        "produces a value and short-circuits the rest. Rule 11 (unresolved) is "
        "a fallback that should fire zero times in a healthy run.",
        styles))
    flow.append(body(
        "The full set of canonical rule labels &mdash; that is, the only "
        "<font face=\"Courier\">source</font> values that "
        "<font face=\"Courier\">scenario_resolved_values</font> ever records "
        "&mdash; is short. Three earlier labels were collapsed into the "
        "canonical <font face=\"Courier\">sum</font> in the twelfth pass; "
        "they no longer appear in the codebase or the resolved table:",
        styles))
    flow.append(data_table(
        ["Canonical label", "Retired predecessors"],
        [
            ["<font face=\"Courier\">observed</font>", "&mdash;"],
            ["<font face=\"Courier\">zero</font>", "&mdash;"],
            ["<font face=\"Courier\">latent</font>", "&mdash;"],
            ["<font face=\"Courier\">sum</font>",
             "<font face=\"Courier\">sum_over_children</font>, "
             "<font face=\"Courier\">sum_over_outflows</font>, "
             "<font face=\"Courier\">sum_same_type</font> &mdash; semantic "
             "role recoverable from <font face=\"Courier\">formula_inputs</font> "
             "structure, see Design_Principles &sect; The canonical sum rule"],
            ["<font face=\"Courier\">alias</font>", "&mdash;"],
            ["<font face=\"Courier\">reverse_mirror</font>", "&mdash;"],
            ["<font face=\"Courier\">arithmetic</font>", "&mdash;"],
            ["<font face=\"Courier\">closure</font>",
             "dormant in starter scenario after B promoted to TS-observed "
             "(sixth pass)"],
            ["<font face=\"Courier\">unresolved</font>",
             "fallback; zero rows expected"],
        ],
        styles, col_widths=(4.5 * cm, 11.5 * cm),
    ))
    flow.append(body(
        "The full per-rule details follow.", styles))

    rules = [
        ("1", "Observed",
         "<font face=\"Courier\">timeseries_id IS NOT NULL</font>. Direct TS lookup. "
         "This is the ground truth; every other rule is downstream of these. "
         "In the starter scenario, 80 variables fire this rule."),
        ("2", "Structural zero",
         "<font face=\"Courier\">formula = '0'</font>. Used for refinery production, "
         "pass-through inventory, and other slots where the value is zero by "
         "physical construction. Roughly 628 variables fire this rule, mostly "
         "non-relational scalars inherited from <font face=\"Courier\">"
         "node_type_default_formulas</font>."),
        ("3", "Latent, with reverse-mirror promotion",
         "<font face=\"Courier\">formula = 'latent()'</font>. Records value = NULL "
         "and source = 'latent'. But first, the rule attempts a "
         "<i>reverse-mirror promotion</i>: if the paired direction "
         "(opposite variable_type, swapped endpoints) has a resolved value, the "
         "rule borrows it via the mirror identity. Without this promotion, a "
         "latent <font face=\"Courier\">outflow_to_padd2</font> on "
         "<font face=\"Courier\">canadian_oil_sands</font> would beat the "
         "reverse-mirror rule and leave the variable NULL despite a known "
         "value on the inflow side of the same edge."),
        ("4", "Sum",
         "<font face=\"Courier\">formula = 'sum'</font>. Sums every value in "
         "<font face=\"Courier\">formula_inputs</font>; NULL inputs are skipped. "
         "This is the twelfth-pass collapse of three earlier labels "
         "(<font face=\"Courier\">sum_over_children</font>, "
         "<font face=\"Courier\">sum_over_outflows</font>, "
         "<font face=\"Courier\">sum_same_type</font>) into one canonical rule. "
         "The semantic role &mdash; aggregation parent, fan-out total, residual "
         "&mdash; is recoverable from the structure of "
         "<font face=\"Courier\">formula_inputs</font> (same-type and same-commodity "
         "&rArr; aggregation; mixed-type at same node &rArr; fan-out), so the "
         "formula text does not need to encode it."),
        ("5", "Single-variable alias",
         "<font face=\"Courier\">formula = bare_variable_id</font> and the "
         "<font face=\"Courier\">formula_inputs</font> list contains at most that "
         "one entry. The variable inherits its value from the named target. "
         "Most aggregate-to-aggregate alias chains fire this rule; in the "
         "starter scenario, 442 variables resolve via alias."),
        ("6", "Reverse mirror",
         "Relational variable with no own resolution; its paired direction "
         "(opposite type, swapped endpoints) has a resolved value. The variable "
         "borrows that value. After the ninth-pass fix, this rule fires "
         "deterministically &mdash; <font face=\"Courier\">build_deps()</font> "
         "now guarantees the paired side is resolved before this side is "
         "evaluated, so 15 mirrors fire reliably (previously 3 by luck of "
         "insertion order)."),
        ("7", "Closure formula",
         "Triggers when the variable is a balancing_item whose formula_inputs "
         "include a mix of inventory, inflow, and outflow variables. Evaluates "
         "<font face=\"Courier\">B = &Delta;S &minus; P + C &minus; &Sigma;I + "
         "&Sigma;O</font>. Now dormant in the starter scenario because B was "
         "promoted to TS-observed in the sixth pass (MCRUA series)."),
        ("8", "Arithmetic combination",
         "Parses the formula text as signed variable references "
         "(<font face=\"Courier\">A &minus; B + C</font>). Used for residual "
         "definitions: <font face=\"Courier\">padd2_other.P = padd2_view.P "
         "&minus; bakken_nd.P &minus; oklahoma.P</font>."),
        ("9", "Unresolved",
         "Records value = NULL, source = 'unresolved'. The fallback. In a "
         "healthy run this fires zero times; non-zero counts indicate a bug or "
         "an unmodelled formula pattern."),
    ]
    flow.append(data_table(
        ["#", "Rule", "Description"],
        [[r[0], f"<b>{r[1]}</b>", r[2]] for r in rules],
        styles, col_widths=(1 * cm, 4 * cm, 11 * cm),
    ))
    flow.append(PageBreak())

    # ---- Section 5: build_deps and the cycle gates ------------------------
    flow.append(heading("5. Building the dependency DAG", 1, styles))
    flow.append(body(
        "<font face=\"Courier\">build_deps()</font> walks every assignment and "
        "produces a dict <font face=\"Courier\">deps[variable] = set of "
        "upstream variable_ids</font>. The set must be acyclic so that "
        "<font face=\"Courier\">graphlib.TopologicalSorter</font> can produce a "
        "valid evaluation order. The build has three sources of dependencies:",
        styles))
    flow.append(body("<b>(a) formula_inputs.</b> Every variable id listed in "
        "<font face=\"Courier\">formula_inputs</font> that exists in the "
        "assignment set becomes a dependency.", styles))
    flow.append(body("<b>(b) sum_over_outflows.</b> The variable depends on every "
        "outflow variable at the same node, except itself.", styles))
    flow.append(body("<b>(c) Reverse-mirror dep, hoisted above the early continue.</b> "
        "When a variable is latent and its paired direction is resolved, the "
        "paired direction is added as a dependency. Two cycle-avoidance gates "
        "protect this: (i) the paired side must not itself be plain latent, "
        "else neither side has a value; and (ii) the paired side must not "
        "already alias from us, else we get a us &harr; paired loop. The second "
        "gate is the <font face=\"Courier\">paired_aliases_us</font> check "
        "added in the ninth pass.",
        styles))
    flow.append(body(
        "Cycle gates matter because the topological sort throws "
        "<font face=\"Courier\">CycleError</font> on the first cycle. The "
        "resolver tolerates many alias and mirror patterns precisely because "
        "of these gates; the cost is a handful of subtle conditions in "
        "<font face=\"Courier\">build_deps()</font> that need to be read together.",
        styles))
    flow.append(PageBreak())

    # ---- Section 6: Closure formula --------------------------------------
    flow.append(heading("6. The closure formula", 1, styles))
    flow.append(body(
        "Rule 8 evaluates the mass-balance closure when the parent variable is "
        "a balancing_item whose inputs span the requisite variable types:",
        styles))
    flow.append(code(
        "B = ΔS  −  P  +  C  −  ΣI  +  ΣO\n\n"
        "ΔS_kbd  =  (S(t) − S(t−1)) / days_in_month(t)     # kbd, not MBBL\n\n"
        "If any required input is NULL at date t, the closure is skipped\n"
        "for that date — the resolver does not silently substitute zero.",
        styles))
    flow.append(body(
        "Skipping rather than zero-imputing matters: a NULL inflow with a "
        "non-zero P and C would produce a spurious B if treated as zero, and "
        "that B would propagate into the USA aggregate. Skipping preserves the "
        "signal that some upstream value is genuinely missing.",
        styles))
    flow.append(body(
        "Since the sixth pass, B at PADD and USA level is TS-observed (MCRUA "
        "series), so closure is no longer the primary path. It remains as a "
        "fallback constraint &mdash; particularly valuable for the Chapter-5 "
        "Claim-4 view, which compares closure-derived B against observed B "
        "and reads the delta as the magnitude of partition-internal latent "
        "flow.",
        styles))
    flow.append(PageBreak())

    # ---- Section 7: Arithmetic formula evaluator --------------------------
    flow.append(heading("7. Arithmetic formula evaluator", 1, styles))
    flow.append(body(
        "Rule 9 parses formula strings of the form "
        "<font face=\"Courier\">A &minus; B &minus; C</font> via a small regex "
        "that pulls out signed terms. Each term must match a variable id present "
        "in <font face=\"Courier\">formula_inputs</font>; anything that fails to "
        "match aborts the rule cleanly and falls through to the next rule. This "
        "limits the surface for parser exploits and keeps the formula language "
        "human-readable.",
        styles))
    flow.append(code(
        "RE_TERM  =  re.compile(r'([+-]?)\\s*([a-z][a-z0-9_]*)')\n\n"
        "for m in RE_TERM.finditer(formula):\n"
        "    sign = -1 if m.group(1) == '-' else 1\n"
        "    tok  = m.group(2)\n"
        "    if tok not in input_set:\n"
        "        ok = False; break\n"
        "    terms.append((sign, tok))",
        styles))
    flow.append(body(
        "Eight variables in the starter scenario fire this rule. They are "
        "structural residual definitions such as "
        "<font face=\"Courier\">padd2_other.P = padd2_view.P &minus; bakken_nd.P "
        "&minus; oklahoma.P</font>, where the residual exists precisely to "
        "soak up the difference between an aggregate total and its enumerated "
        "constituents.",
        styles))
    flow.append(PageBreak())

    # ---- Section 8: Persistence + audit -----------------------------------
    flow.append(heading("8. Persistence and the audit trail", 1, styles))
    flow.append(body(
        "After the dispatch loop finishes, the resolver clears the scenario&rsquo;s "
        "existing rows in <font face=\"Courier\">scenario_resolved_values</font> "
        "and writes the new ones via <font face=\"Courier\">execute_values</font> "
        "in pages of 5,000. The audit row in "
        "<font face=\"Courier\">scenario_resolver_runs</font> is updated with the "
        "completion timestamp, the elapsed milliseconds, and the dispatch counts "
        "as JSONB.",
        styles))
    flow.append(body(
        "If the resolver crashes mid-way, the open audit row with NULL "
        "completed_at is itself a useful signal &mdash; it tells the next operator "
        "that a run was started but did not finish. The resolved-values table "
        "is unaffected by a crash because the DELETE/INSERT pair runs inside "
        "a transaction.",
        styles))
    flow.append(PageBreak())

    # ---- Section 9: Bugs the framework has hit ----------------------------
    flow.append(heading("9. Bugs encountered and fixed", 1, styles))
    flow.append(heading("9.1  The latent-mirror cycle", 2, styles))
    flow.append(body(
        "Original implementation: a latent variable would add its paired "
        "direction as a dependency unconditionally. When the paired direction "
        "was also latent and aliased to its own paired side, this produced a "
        "two-node cycle (us &rarr; paired &rarr; us) and TopologicalSorter raised "
        "<font face=\"Courier\">CycleError</font>. Fix: require the paired side "
        "to be non-latent before adding the dep (first cycle gate).",
        styles))
    flow.append(heading("9.2  The latent short-circuit that hid reverse-mirror", 2, styles))
    flow.append(body(
        "Rule 3 (latent) ran before Rule 7 (reverse-mirror), so a variable "
        "explicitly marked latent would record value = NULL even when its paired "
        "direction had an observed value. Fix: promote the mirror inside Rule 3 "
        "&mdash; if the paired side is resolved, borrow its value first and only "
        "fall through to NULL otherwise. Together with the build_deps hoist (so "
        "that latent variables get their paired-side dep added before being "
        "skipped), this lifted mirror dispatch from 3 (by luck of insertion "
        "order) to 15 (deterministic).",
        styles))
    flow.append(heading("9.3  The alias-rule wrapper bug", 2, styles))
    flow.append(body(
        "Rule 6 matches when <font face=\"Courier\">formula in by_id</font>, "
        "i.e. the formula text is itself a bare variable id. Earlier migration "
        "scripts wrote <font face=\"Courier\">formula = 'alias(variable_id)'</font> "
        "with the wrapper string; rule 6 missed those, and they slipped past on "
        "Rule 10 (same-type rollup) by coincidence. The fix is in the migration "
        "scripts: <font face=\"Courier\">formula = bare_variable_id</font>, with "
        "the wrapper retained only as the display label in <font face=\"Courier\">"
        "formula_used</font> on resolved rows.",
        styles))
    flow.append(PageBreak())

    # ---- Section 10: Dispatch counts in the current state -----------------
    flow.append(heading("10. Dispatch counts in the current starter scenario", 1, styles))

    # Pull live numbers from the latest resolver run on the starter scenario,
    # so the PDF stays in sync with whatever state the resolver was last in.
    run_id, completed_at, n_rows, dispatch = fetch_latest_dispatch()
    if dispatch:
        completed_str = completed_at.strftime("%Y-%m-%d") if completed_at else "(unknown date)"
        n_rows_str = f"{n_rows:,}" if n_rows else "(unknown)"
        flow.append(body(
            f"Live state at PDF generation: run_id = {run_id}, completed "
            f"{completed_str}. {n_rows_str} rows persisted to "
            "<font face=\"Courier\">scenario_resolved_values</font>.",
            styles))
        rows = []
        labels = [
            ("observed",        "observed (TS lookup)"),
            ("zero",            "zero (structural)"),
            ("latent",          "latent (incl. reverse-mirror failures)"),
            ("alias",           "alias (single-variable)"),
            ("reverse_mirror",  "reverse-mirror (promotion + Rule 6)"),
            ("arithmetic",      "arithmetic"),
            ("sum",             "sum (canonical, collapses sum_over_*/sum_same_type)"),
            ("closure",         "closure (B = &Delta;S &minus; P + C &minus; &Sigma;I + &Sigma;O)"),
            ("unresolved",      "<b>unresolved</b>"),
        ]
        for key, label in labels:
            count = dispatch.get(key, 0)
            count_str = f"<b>{count}</b>" if key == "unresolved" else str(count)
            rows.append([label, count_str])
        flow.append(data_table(
            ["Rule", "Count"], rows, styles, col_widths=(11 * cm, 4 * cm),
        ))
    else:
        flow.append(body(
            "No completed resolver run found in "
            "<font face=\"Courier\">scenario_resolver_runs</font> for the "
            f"<font face=\"Courier\">{SCENARIO}</font> scenario. Run "
            "<font face=\"Courier\">resolve_scenario.py</font> to populate.",
            styles))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(body(
        "<b>Zero unresolved</b> is the load-bearing invariant. Every declared "
        "variable has a value or an explicit latent marker. The downstream "
        "consumers can rely on the presence of a row for every (variable, date) "
        "pair, and on the source column telling them whether the NULL is by "
        "design (latent) or by failure (unresolved, which is an unmodelled "
        "case that needs attention).",
        styles))

    # ---- Build ------------------------------------------------------------
    p = build_pdf(OUT, flow,
                  header_text="Resolver Walkthrough &mdash; Pedro Porfirio, NOVA IMS")
    print(f"Wrote {p}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
