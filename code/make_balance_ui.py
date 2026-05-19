"""Generate a self-contained HTML balance UI.

Output: oil_network_balance.html in the same directory.

Layout:
  - Rows: nodes, starting from roots (no parent). Expandable: click [+] to show children.
  - Columns at a selected date: P, C, I_total, O_total, B, S (level mbbl), dS (kbd)
  - Click any cell -> the time series for that (node, variable) renders on the right.

Variable resolution (matches v_aggregate_balance / v_aggregation_consistency):
  - Direct TS binding: value comes from timeseries_data
  - One-level mirror dereference (formula = '<other_var_id>'): resolve to that var's TS
  - Aggregates' balancing_item: use v_aggregate_balance (since the formula evaluator
    isn't built yet, we read the SQL view's pre-computed B for the 6 region aggregates)

Self-contained: data embedded as JSON, Plotly.js from CDN.
"""
from __future__ import annotations
from paths import HTML_DIR

import json
import re
from collections import defaultdict
from pathlib import Path

import psycopg2

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_balance.html"

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# ----------------------------------------------------------------------------- query

def fetch():
    """Returns:
       assets: list of {asset_id, name, kind, node_class, node_subtype}
       parent_map: {child_id: (parent_id, source)}
       node_values: {node_id: {variable_letter: {date_str: value, ...}}}
                    variable_letter in P/C/I/O/B/S/dS
    """
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.name, a.kind, a.node_class, a.node_subtype
            FROM oil_network.assets a ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        assets = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT v.variable_id, v.node_id, v.variable_type
            FROM oil_network.variables v
        """)
        variables = [{"variable_id": r[0], "node_id": r[1], "variable_type": r[2]}
                     for r in cur.fetchall()]

        # Parent map: aggregation links via formula_inputs.
        # An aggregation link is parent.X.formula_inputs = [child1.X, child2.X, ...] —
        # i.e., parent_var.variable_type == child_var.variable_type AND parent_node != child_node.
        # Only clean *sum* aggregations qualify as parenthood:
        #   - formula IS NULL / empty: parent is a TS-bound aggregate whose
        #     formula_inputs were declared by add_aggregation_constituents.py
        #     (e.g. padd2_view.P -> bakken_nd, oklahoma_conventional, padd2_other)
        #   - formula = 'sum': explicit aggregation (canonical label, post-twelfth-pass)
        # Subtraction / arithmetic formulas DO NOT establish parenthood — those
        # operands are siblings, not children (e.g. padd2_other has
        # formula 'padd2_view - bakken_nd - oklahoma_conventional'; that does
        # NOT make oklahoma_conventional a child of padd2_other).
        # Single-variable aliases (mirrors, finer-resolution views) also don't
        # establish parenthood — the mirror inherits, it doesn't parent.
        cur.execute("""
            SELECT v.node_id          AS parent_node,
                   child_v.node_id    AS child_node
            FROM oil_network.variable_assignments va
            JOIN oil_network.variables v ON v.variable_id = va.variable_id
            CROSS JOIN LATERAL unnest(va.formula_inputs) AS u(child_var_id)
            JOIN oil_network.variables child_v ON child_v.variable_id = u.child_var_id
            WHERE v.variable_type = child_v.variable_type
              AND v.node_id <> child_v.node_id
              AND (va.formula IS NULL
                   OR va.formula = ''
                   OR va.formula = 'sum')
        """)
        parent_via_formula = {}
        for row in cur.fetchall():
            parent_node, child_node = row[0], row[1]
            if child_node not in parent_via_formula:
                parent_via_formula[child_node] = parent_node

        # The big values query: per (node, date) pivot of P/C/I/O/B/S + dS.
        cur.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (timeseries_id, observation_date)
                       timeseries_id, observation_date, value
                FROM oil_network.timeseries_data
                ORDER BY timeseries_id, observation_date, saved_date DESC
            ),
            days AS (
                SELECT observation_date,
                       EXTRACT(DAY FROM (date_trunc('month', observation_date)
                                         + interval '1 month - 1 day'))::numeric AS d
                FROM (SELECT DISTINCT observation_date FROM latest) x
            ),
            resolved_ts AS (
                -- (a) Direct TS binding
                SELECT v.variable_id, va.timeseries_id
                FROM oil_network.variables v
                JOIN oil_network.variable_assignments va USING (variable_id)
                WHERE va.timeseries_id IS NOT NULL
                UNION ALL
                -- (b) Forward mirror: formula = '<paired_var_id>', use that variable's TS
                SELECT v.variable_id, mva.timeseries_id
                FROM oil_network.variables v
                JOIN oil_network.variable_assignments va USING (variable_id)
                JOIN oil_network.variable_assignments mva
                  ON mva.variable_id = va.formula
                 AND mva.timeseries_id IS NOT NULL
                WHERE va.timeseries_id IS NULL
                  AND va.formula IS NOT NULL
                  AND va.formula NOT IN ('0', 'latent()', 'sum')
                  AND cardinality(va.formula_inputs) = 1
                UNION ALL
                -- (c) Reverse mirror: for a relational variable (inflow/outflow) without
                --     a direct TS, look up the paired edge variable (opposite direction,
                --     same endpoints, same commodity). If that one is TS-bound, use its TS.
                --     Same edge value, two variable IDs.
                SELECT v.variable_id, paired_va.timeseries_id
                FROM oil_network.variables v
                JOIN oil_network.variable_assignments my_va
                  ON my_va.variable_id = v.variable_id AND my_va.timeseries_id IS NULL
                JOIN oil_network.variables paired_v
                  ON paired_v.node_id         = v.related_node_id
                 AND paired_v.related_node_id = v.node_id
                 AND paired_v.commodity       = v.commodity
                 AND paired_v.variable_type   = CASE v.variable_type
                       WHEN 'inflow' THEN 'outflow'
                       WHEN 'outflow' THEN 'inflow'
                     END
                JOIN oil_network.variable_assignments paired_va
                  ON paired_va.variable_id = paired_v.variable_id
                 AND paired_va.timeseries_id IS NOT NULL
                WHERE v.variable_type IN ('inflow', 'outflow')
                  AND v.related_node_id IS NOT NULL
            ),
            node_var_values AS (
                SELECT v.node_id, v.variable_type, td.observation_date, td.value
                FROM oil_network.variables v
                JOIN resolved_ts rt ON rt.variable_id = v.variable_id
                JOIN latest td ON td.timeseries_id = rt.timeseries_id
            ),
            agg AS (
                SELECT node_id, observation_date,
                    MAX(value) FILTER (WHERE variable_type='production')     AS p,
                    MAX(value) FILTER (WHERE variable_type='consumption')    AS c,
                    SUM(value) FILTER (WHERE variable_type='inflow')         AS i,
                    SUM(value) FILTER (WHERE variable_type='outflow')        AS o,
                    MAX(value) FILTER (WHERE variable_type='balancing_item') AS b,
                    MAX(value) FILTER (WHERE variable_type='inventory')      AS s_mbbl
                FROM node_var_values
                GROUP BY node_id, observation_date
            )
            SELECT a.node_id, a.observation_date::text,
                   a.p, a.c, a.i, a.o, a.b, a.s_mbbl,
                   (a.s_mbbl - LAG(a.s_mbbl) OVER (PARTITION BY a.node_id ORDER BY a.observation_date))
                     / d.d AS ds_kbd
            FROM agg a JOIN days d USING (observation_date)
            ORDER BY a.node_id, a.observation_date
        """)
        rows = cur.fetchall()

        # Region-aggregate B from v_aggregate_balance (formula closure)
        cur.execute("""
            SELECT region_node, observation_date::text, b_kbd, ds_kbd
            FROM oil_network.v_aggregate_balance
        """)
        agg_b = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}

        # Per-scenario node roles (balance vs constraint)
        cur.execute("""
            SELECT node_id, role FROM oil_network.scenario_node_role
            WHERE scenario_id = 'starter_us_crude_2015_2025'
        """)
        node_roles = {r[0]: r[1] for r in cur.fetchall()}

        # Aggregation sum_kids per (node_id, variable_type, date) — walks
        # formula_inputs of each parent variable, sums resolved constituents.
        # This is the structurally correct "sum-of-children" for the balance UI:
        # it follows declared aggregation arrows (not raw partition-children
        # column walks), so it excludes internal inter-PADD flows for I/O.
        cur.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (timeseries_id, observation_date)
                       timeseries_id, observation_date, value
                FROM oil_network.timeseries_data
                ORDER BY timeseries_id, observation_date, saved_date DESC
            ),
            resolved_ts AS (
                SELECT v.variable_id, va.timeseries_id
                FROM oil_network.variables v
                JOIN oil_network.variable_assignments va USING (variable_id)
                WHERE va.timeseries_id IS NOT NULL
                UNION ALL
                SELECT v.variable_id, mva.timeseries_id
                FROM oil_network.variables v
                JOIN oil_network.variable_assignments va USING (variable_id)
                JOIN oil_network.variable_assignments mva
                  ON mva.variable_id = va.formula AND mva.timeseries_id IS NOT NULL
                WHERE va.timeseries_id IS NULL AND va.formula IS NOT NULL
                  AND va.formula NOT IN ('0', 'latent()', 'sum')
                  AND cardinality(va.formula_inputs) = 1
                UNION ALL
                SELECT v.variable_id, paired_va.timeseries_id
                FROM oil_network.variables v
                JOIN oil_network.variable_assignments my_va
                  ON my_va.variable_id = v.variable_id AND my_va.timeseries_id IS NULL
                JOIN oil_network.variables paired_v
                  ON paired_v.node_id = v.related_node_id
                 AND paired_v.related_node_id = v.node_id
                 AND paired_v.commodity = v.commodity
                 AND paired_v.variable_type = CASE v.variable_type
                       WHEN 'inflow' THEN 'outflow'
                       WHEN 'outflow' THEN 'inflow' END
                JOIN oil_network.variable_assignments paired_va
                  ON paired_va.variable_id = paired_v.variable_id
                 AND paired_va.timeseries_id IS NOT NULL
                WHERE v.variable_type IN ('inflow', 'outflow')
                  AND v.related_node_id IS NOT NULL
            )
            SELECT v.node_id, v.variable_type, td.observation_date::text,
                   SUM(td.value) AS sum_kids
            FROM oil_network.variables v
            JOIN oil_network.variable_assignments va USING (variable_id)
            CROSS JOIN LATERAL unnest(va.formula_inputs) AS u(child_var_id)
            JOIN oil_network.variables child_v ON child_v.variable_id = u.child_var_id
            JOIN resolved_ts rt ON rt.variable_id = u.child_var_id
            JOIN latest td ON td.timeseries_id = rt.timeseries_id
            WHERE cardinality(va.formula_inputs) > 0
              -- Only true cross-node, same-variable-type aggregation arrows.
              -- Excludes (a) same-node closure formulas (e.g. B = P+C+S+I+O on the
              -- same node) and (b) cross-type formulas (e.g. a fan-out 'sum' at a junction).
              AND child_v.node_id      <> v.node_id
              AND child_v.variable_type = v.variable_type
            GROUP BY v.node_id, v.variable_type, td.observation_date
        """)
        # Map variable_type to letter
        VTYPE_LETTER = {"production": "P", "consumption": "C",
                        "inventory": "S", "balancing_item": "B",
                        "inflow": "I", "outflow": "O"}
        agg_sums = defaultdict(lambda: defaultdict(dict))  # node -> letter -> date -> value
        for row in cur.fetchall():
            nid, vtype, date_str, sumval = row
            letter = VTYPE_LETTER.get(vtype)
            if letter and sumval is not None:
                agg_sums[nid][letter][date_str] = float(sumval)

        # Arithmetic formulas (multi-term subtraction / addition of variable_ids).
        # These are the residual and basin-state derivations:
        #   padd2_other = padd2_view - bakken_nd - oklahoma_conventional
        #   permian_tx  = permian - permian_nm
        #   etc.
        # We pre-parse the formula string into [(sign, ref_node, ref_letter), ...]
        # and ship the list to JS, which evaluates at render time as the layer
        # between "own TS" and "tree-walk fallback" in resolvedValueAt().
        # Closure formulas (delta()/sum() pseudo-funcs) and single-input aliases
        # are intentionally skipped — they're handled elsewhere.
        cur.execute("""
            SELECT v.node_id, v.variable_type, va.formula, va.formula_inputs
            FROM oil_network.variable_assignments va
            JOIN oil_network.variables v ON v.variable_id = va.variable_id
            WHERE va.scenario_id = 'starter_us_crude_2015_2025'
              AND va.formula IS NOT NULL
              AND va.formula NOT IN ('', '0', 'latent()', 'sum')
              AND cardinality(va.formula_inputs) >= 2
        """)
        var_lookup = {x["variable_id"]: x for x in variables}
        re_token = re.compile(r'([+\-])?\s*([A-Za-z][A-Za-z0-9_]*)')
        formulas_out = defaultdict(dict)
        for nid, vtype, formula, inputs in cur.fetchall():
            input_set = set(inputs or [])
            terms = []
            ok = True
            for m in re_token.finditer(formula):
                sign = -1 if m.group(1) == '-' else 1
                tok = m.group(2)
                if tok not in input_set:
                    ok = False  # likely a closure pseudo-func (delta, sum, inventory...)
                    break
                ref = var_lookup.get(tok)
                if not ref:
                    ok = False
                    break
                ref_letter = VTYPE_LETTER.get(ref["variable_type"])
                if not ref_letter:
                    ok = False
                    break
                terms.append([sign, ref["node_id"], ref_letter])
            if ok and terms:
                parent_letter = VTYPE_LETTER.get(vtype)
                if parent_letter:
                    formulas_out[nid][parent_letter] = terms

    # Build node_values: { node_id: { var_letter: { date: value } } }
    node_values = defaultdict(lambda: {k: {} for k in ("P", "C", "I", "O", "B", "S", "dS")})
    for nid, obs_date, p, c, i, o, b, s_mbbl, ds in rows:
        nv = node_values[nid]
        if p is not None:      nv["P"][obs_date] = p
        if c is not None:      nv["C"][obs_date] = c
        if i is not None:      nv["I"][obs_date] = i
        if o is not None:      nv["O"][obs_date] = o
        if b is not None:      nv["B"][obs_date] = b
        if s_mbbl is not None: nv["S"][obs_date] = s_mbbl
        if ds is not None:     nv["dS"][obs_date] = ds

    # Overlay region B + ds from v_aggregate_balance (computed closure)
    for (nid, obs_date), (b, ds_kbd) in agg_b.items():
        if b is not None:
            node_values[nid]["B"][obs_date] = b
        if ds_kbd is not None and obs_date not in node_values[nid]["dS"]:
            node_values[nid]["dS"][obs_date] = ds_kbd

    return (assets, variables, parent_via_formula, dict(node_values),
            node_roles, dict(agg_sums), dict(formulas_out))


# ----------------------------------------------------------------------------- structure

ROOT_NODES = {"usa_view", "canadian_oil_sands", "foreign_supply", "foreign_export_destination"}

EXPLICIT_PADD = {
    "padd1_other": "1", "padd2_other": "2", "padd3_other": "3", "padd4_other": "4", "padd5_other": "5",
    "texas_state_view": "3", "montana_state_view": "4",
}


def build_parent_map(assets, parent_via_formula):
    """parent_via_formula is the single source of truth, sourced from
    v_partition_tree by the caller (eighteenth-pass simplification).

    Earlier versions carried a 35-entry hardcoded `structural` dict here;
    those overrides duplicated v_partition_tree or, for constraint /
    observational aggregates, papered over their absence from the partition
    spine. The eighteenth-pass data migration declared those constraint
    nodes as inventory-children of their natural display parent, so
    v_partition_tree now contains every relation the renderer needs.

    Returns: {child_node_id: (parent_node_id, 'formula_inputs')}.
    """
    return dict((c, (p, "formula_inputs")) for c, p in parent_via_formula.items())


def fetch_locations():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, l.padd
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.kind = 'physical'
        """)
        return {r[0]: r[1] for r in cur.fetchall()}


# ----------------------------------------------------------------------------- payload

def build_payload(assets, variables, parent_via_formula, node_values, node_roles, agg_sums, formulas):
    parent_map = build_parent_map(assets, parent_via_formula)

    # Derive a ΔS sum_kids from S sum_kids using LAG-per-date / days-in-month.
    # (The SQL agg_sums has S sum_kids; we synthesise dS sum_kids here.)
    from datetime import date as _date
    def days_in_month(date_str: str) -> int:
        y, m, _d = date_str.split("-")
        y = int(y); m = int(m)
        next_m = (m % 12) + 1
        next_y = y + (1 if m == 12 else 0)
        from datetime import date as _dt
        return ( _dt(next_y, next_m, 1) - _dt(y, m, 1) ).days
    for nid, by_letter in list(agg_sums.items()):
        s_series = by_letter.get("S")
        if not s_series:
            continue
        ds_series = {}
        prev_d, prev_v = None, None
        for d in sorted(s_series.keys()):
            v = s_series[d]
            if prev_v is not None:
                ds_series[d] = (v - prev_v) / days_in_month(d)
            prev_d, prev_v = d, v
        if ds_series:
            agg_sums[nid]["dS"] = ds_series

    locations = fetch_locations()
    for a in assets:
        nid = a["asset_id"]
        if nid in parent_map or nid in ROOT_NODES or a["kind"] != "physical":
            continue
        padd_raw = locations.get(nid) or EXPLICIT_PADD.get(nid)
        if padd_raw:
            m = re.search(r"([1-5])", str(padd_raw))
            if m:
                parent_map[nid] = (f"padd{m.group(1)}_view", "geography")

    # Children map
    children = defaultdict(list)
    for c, (p, _src) in parent_map.items():
        children[p].append(c)
    for p in children:
        children[p].sort()

    # Per-node summary
    nodes_out = {}
    for a in assets:
        nid = a["asset_id"]
        vals = node_values.get(nid, {k: {} for k in "PCIOBSdS".replace("d", "")} | {"dS": {}})
        # has_data_for: which variables have ANY date with a value
        has_for = {k: len(vals.get(k, {})) > 0 for k in ("P", "C", "I", "O", "B", "S", "dS")}
        # Role priority: explicit per-scenario tag from scenario_node_role
        # overrides any kind-based default. Physical nodes default to 'balance'
        # (implicit partition leaves); abstract nodes without a tag stay None.
        explicit_role = node_roles.get(nid)
        if explicit_role:
            role = explicit_role
        elif a["kind"] == "physical":
            role = "balance"
        else:
            role = None
        nodes_out[nid] = {
            "name":     a["name"],
            "kind":     a["kind"],
            "node_class": a["node_class"],
            "node_subtype": a["node_subtype"],
            "role":     role,
            "parent":   None,
            "children": children.get(nid, []),
            "has":      has_for,
            "values":   vals,
            "sum_kids": agg_sums.get(nid, {}),
        }
    # Fix parent extraction (parent_map is dict of tuples)
    for a in assets:
        nid = a["asset_id"]
        p = parent_map.get(nid)
        nodes_out[nid]["parent"] = p[0] if p else None

    # Roots split by role: balance roots build the partition tree;
    # constraint roots fill the separate "Constraints" section.
    all_roots = sorted([nid for nid in nodes_out if nodes_out[nid]["parent"] is None])
    balance_roots    = [nid for nid in all_roots if nodes_out[nid]["role"] != "constraint"]
    constraint_roots = [nid for nid in all_roots if nodes_out[nid]["role"] == "constraint"]

    # Additionally: nodes that have a parent in the balance tree but are themselves
    # constraints (e.g. permian under usa_view) should be MOVED out of the balance
    # tree's child list and into the constraints list.
    for parent_nid, p in list(nodes_out.items()):
        cleaned_children = [c for c in p["children"]
                            if nodes_out.get(c, {}).get("role") != "constraint"]
        if len(cleaned_children) != len(p["children"]):
            p["children_partition"] = cleaned_children
            p["children_constraints"] = [c for c in p["children"]
                                          if nodes_out.get(c, {}).get("role") == "constraint"]
            p["children"] = cleaned_children  # default expansion uses partition children only
        else:
            p["children_partition"] = p["children"]
            p["children_constraints"] = []

    # Promote constraint nodes that had a balance parent to constraint_roots
    # (so they appear in the Constraints section as a flat list)
    for nid, n in nodes_out.items():
        if n["role"] == "constraint" and n["parent"] is not None:
            parent_role = nodes_out.get(n["parent"], {}).get("role")
            if parent_role != "constraint" and nid not in constraint_roots:
                constraint_roots.append(nid)
    constraint_roots = sorted(set(constraint_roots))

    # Date list
    all_dates = set()
    for nv in node_values.values():
        for var_dict in nv.values():
            all_dates.update(var_dict.keys())
    dates = sorted(all_dates)

    # Pick a smart default date: the most recent date where usa_view has values
    # for P, C, and S together. Falls back to the latest date with ANY usa_view
    # data, then to the global last date. Avoids landing on a date where the
    # observed US-level series have no data (e.g. 2027 STEO horizon).
    default_date = dates[-1] if dates else None
    usa = node_values.get("usa_view", {})
    p_dates = set(usa.get("P", {}).keys())
    c_dates = set(usa.get("C", {}).keys())
    s_dates = set(usa.get("S", {}).keys())
    full_set = p_dates & c_dates & s_dates
    if full_set:
        default_date = max(full_set)
    elif p_dates:
        default_date = max(p_dates)

    # Group constraint nodes by category (for clearer rendering in the UI)
    CONSTRAINT_CATEGORIES = [
        ("Basin aggregates",
         "Cross-PADD basin rollups that re-slice physical production already in the PADD partition. "
         "Their observations derive state-residual partition members.",
         ["permian", "bakken", "eagle_ford"]),
        ("Cross-cut state and STEO aggregates",
         "Alternative slicings of the same physical assets: state-level production aggregates, "
         "SPR rollup, and STEO subtotal. Provide derivation constraints + cross-validation.",
         ["texas_state_view", "montana_state_view",
          "spr_total", "usa_lower48_excl_gom_view"]),
        ("System boundary",
         "Foreign sources and sinks. Outside the US partition; provide boundary inflow/outflow "
         "values that bind the partition's per-PADD I/O variables.",
         ["canadian_oil_sands", "foreign_supply", "foreign_export_destination"]),
    ]
    constraint_groups = []
    seen = set()
    for label, blurb, members in CONSTRAINT_CATEGORIES:
        group_nodes = [n for n in members if n in constraint_roots]
        if group_nodes:
            constraint_groups.append({"label": label, "blurb": blurb, "nodes": group_nodes})
            seen.update(group_nodes)
    # Anything in constraint_roots not in a category goes in "Other"
    leftover = [n for n in constraint_roots if n not in seen]
    if leftover:
        constraint_groups.append({"label": "Other constraints", "blurb": "", "nodes": leftover})

    return {"nodes": nodes_out,
            "balance_roots":     balance_roots,
            "constraint_roots":  constraint_roots,
            "constraint_groups": constraint_groups,
            "dates": dates,
            "default_date": default_date,
            "formulas": formulas}


# ----------------------------------------------------------------------------- html

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>oil_network &mdash; balance equation per node</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root { --bg: #f7f7f5; --panel: #fff; --border: #d0d0c8; --hover: #eef3ff;
          --selected: #c8d4f7; --abstract: #6a5acd; --physical: #2a9d8f; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 12.5px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: var(--bg); color: #222; }
  header { padding: 8px 16px; background: #1a1f2c; color: #fff;
           display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; }
  header select { font: inherit; padding: 3px 6px; border-radius: 3px; border: 1px solid #444;
                  background: #2a3142; color: #fff; }
  header .legend { margin-left: auto; font-size: 11px; color: #ccc; }
  header .legend span { display: inline-block; padding: 1px 6px; margin-left: 6px;
                         border-radius: 2px; color: #fff; font-size: 9.5px; }
  header .legend .ab { background: var(--abstract); }
  header .legend .ph { background: var(--physical); }
  .layout { display: grid; grid-template-columns: 1fr 480px; height: calc(100vh - 38px); }
  .table-pane { overflow: auto; background: #fff; }
  .chart-pane { background: #fff; border-left: 1px solid var(--border);
                padding: 14px 16px; overflow-y: auto; }
  table.balance { width: 100%; border-collapse: collapse; font-size: 12px; }
  table.balance th, table.balance td { padding: 4px 8px; border-bottom: 1px solid #eee; }
  table.balance th { background: #f0f0eb; position: sticky; top: 0; z-index: 2;
                     text-align: right; font-weight: 600; font-size: 11px; color: #555;
                     border-bottom: 2px solid var(--border); }
  table.balance th:first-child { text-align: left; }
  table.balance td { font-family: ui-monospace, "SF Mono", Menlo, monospace;
                      text-align: right; cursor: default; }
  table.balance td.node-cell { text-align: left; cursor: pointer; user-select: none;
                                font-family: -apple-system, "Segoe UI", sans-serif; }
  table.balance td.node-cell:hover { background: var(--hover); }
  table.balance td.has-data { cursor: pointer; }
  table.balance td.has-data:hover { background: var(--hover); }
  table.balance td.missing { color: #c0c0c0; }
  table.balance tr.selected td { background: var(--selected); }
  table.balance tr.selected td.has-data:hover { background: var(--selected); }
  table.balance tr.section-row td { background: #2a3142; color: #fff;
                                     padding: 6px 10px; font-size: 12px; }
  table.balance tr.subsection-row td { background: #f0eee3; color: #444;
                                        padding: 4px 10px; font-size: 11.5px;
                                        border-top: 1px solid var(--border);
                                        border-bottom: 1px solid var(--border); }
  table.balance tr.subsection-row .blurb { color: #777; font-weight: normal;
                                            font-size: 10.5px; margin-left: 6px; }
  /* Dual-value cell: observed (top, black) vs sum-of-children (below, teal).
     When they match, only the observed value is shown to keep the table tidy. */
  table.balance td .observed { color: #222; display: block; line-height: 1.2; }
  table.balance td .sum-kids { color: #2a9d8f; font-size: 91%; display: block;
                                line-height: 1.2; margin-top: 1px; }
  table.balance td.no-obs .observed { display: none; }
  table.balance td.no-obs .sum-kids { color: #2a9d8f; font-style: italic; }
  table.balance td.no-obs-or-sum .observed { color: #222; }
  /* Intra-partition annotation: small grey tag below the sum-of-children value
     showing how much flow is exchanged between this parent's children
     (e.g. PADD-to-PADD volume under usa_view). Doesn't enter the boundary
     sum but is shown so the magnitude isn't hidden. */
  table.balance td .intra { color: #888; font-size: 84%; display: block;
                            line-height: 1.15; margin-top: 1px; font-style: italic; }
  /* Sum-vs-observed mismatch coloring (Pedro's three-state spec):
       green  = match within tolerance (default cell, no extra class)
       red    = all children have values and sum disagrees by > 5 units
                — a real partition divergence
       yellow = some children missing or partial data
                — can't verify; gap is "unknown contribution" */
  table.balance td.mismatch-red    { background: #fde8e8; }
  table.balance td.mismatch-red    .sum-kids { color: #c0392b; font-weight: 600; }
  table.balance td.mismatch-yellow { background: #fff8e1; }
  table.balance td.mismatch-yellow .sum-kids { color: #b78a00; }
  .toggle { display: inline-block; width: 14px; color: #888; }
  .role-tag { display: inline-block; padding: 0 4px; border-radius: 2px; font-size: 9px;
              color: #fff; margin-left: 4px; opacity: 0.75; vertical-align: middle; }
  .role-tag.balance    { background: #2a9d8f; }
  .role-tag.constraint { background: #d4a017; }
  .kind-tag { display: inline-block; padding: 0 4px; border-radius: 2px; font-size: 9px;
              color: #fff; margin-left: 4px; opacity: 0.75; vertical-align: middle; }
  .kind-tag.abstract { background: var(--abstract); }
  .kind-tag.physical { background: var(--physical); }
  .subtype { color: #999; font-size: 10.5px; margin-left: 6px; }
  #chart { width: 100%; height: 45vh; }
  .chart-meta { margin-top: 10px; padding: 10px 12px; background: #f7f7f5;
                border: 1px solid var(--border); border-radius: 4px; font-size: 12px;
                font-family: ui-monospace, Menlo, monospace; }
  .placeholder { color: #999; padding: 30px; text-align: center; font-style: italic; }
</style>
</head>
<body>
<header>
  <h1>oil_network &middot; balance equation per node</h1>
  <label>date <select id="date-picker"></select></label>
  <div class="legend">
    <span class="ab">abs</span>abstract aggregate &middot;
    <span class="ph">phy</span>physical asset &middot;
    <span style="color:#222">observed</span> /
    <span style="color:#2a9d8f">sum-of-children</span>
    <br><span style="color:#999;font-size:10px">click a cell to see the variable's time series &middot; teal sum shown only when materially different from observed</span>
  </div>
</header>
<div class="layout">
  <div class="table-pane">
    <table class="balance" id="tbl">
      <thead>
        <tr>
          <th>Node</th>
          <th title="Production">P</th>
          <th title="Consumption">C</th>
          <th title="Total inflow (sum of all inbound flows)">I</th>
          <th title="Total outflow (sum of all outbound flows)">O</th>
          <th title="Balancing item">B</th>
          <th title="Inventory level (MBBL snapshot)">S (mbbl)</th>
          <th title="Inventory change converted to kbd">&Delta;S</th>
        </tr>
      </thead>
      <tbody id="tbl-body-balance"></tbody>
      <tbody id="tbl-body-constraints"></tbody>
    </table>
  </div>
  <div class="chart-pane">
    <div id="chart"></div>
    <div class="chart-meta" id="chart-meta"></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const NODES = DATA.nodes;
const BALANCE_ROOTS     = DATA.balance_roots;
const CONSTRAINT_GROUPS = DATA.constraint_groups;
const DATES = DATA.dates;
const FORMULAS = DATA.formulas || {};
const VAR_LETTERS = ["P","C","I","O","B","S","dS"];
const VAR_LABEL = {
  P: "Production",
  C: "Consumption",
  I: "Inflow (sum)",
  O: "Outflow (sum)",
  B: "Balancing item",
  S: "Inventory level (MBBL)",
  dS: "Delta inventory (kbd)"
};
const VAR_UNIT = {
  P: "kbd", C: "kbd", I: "kbd", O: "kbd", B: "kbd",
  S: "mbbl", dS: "kbd",
};

// Populate date picker
const datePicker = document.getElementById("date-picker");
for (const d of DATES) {
  const opt = document.createElement("option");
  opt.value = d;
  opt.textContent = d;
  datePicker.appendChild(opt);
}
// Default to the latest date where usa_view has full P/C/S observations
// (computed in Python; falls back to global latest if that fails)
if (DATA.default_date) datePicker.value = DATA.default_date;
else if (DATES.length > 0) datePicker.value = DATES[DATES.length - 1];

// Expanded state: set of node ids currently expanded
const expanded = new Set();  // start collapsed

// Build the visible row list given current expansion state, walking from a list of roots.
function visibleFrom(roots) {
  const out = [];
  function walk(nid, depth) {
    out.push({nid, depth});
    if (expanded.has(nid)) {
      const ch = NODES[nid].children || [];
      for (const c of ch) walk(c, depth + 1);
    }
  }
  for (const r of roots) walk(r, 0);
  return out;
}

function fmtNum(v, unit) {
  if (v === null || v === undefined) return null;
  // S is large levels (hundreds of thousands); kbd values are typically -20k to +20k
  return unit === "mbbl" ? v.toFixed(0) : v.toFixed(1);
}

// Resolved value at a node for a variable letter, in priority order:
//   1) the node's own value (own TS, or one-input alias pre-resolved by SQL);
//   2) the pre-parsed arithmetic formula if any (e.g. padd2_other = padd2_view − bakken_nd − oklahoma_conventional);
//   3) the recursive tree-sum of children's resolved values.
// "TS supersedes" naturally — if a node has its own value we return it without descending.
function resolvedValueAt(nid, varLetter, date) {
  const n = NODES[nid];
  if (!n) return null;
  const own = n.values && n.values[varLetter] ? n.values[varLetter][date] : null;
  if (own !== null && own !== undefined) return own;
  const fromFormula = evalFormulaAt(nid, varLetter, date);
  if (fromFormula !== null && fromFormula !== undefined) return fromFormula;
  // Fall back to the boundary slot — that's the meaningful "what arrives at /
  // leaves this node" if it has no own value.
  const r = treeSumKids(nid, varLetter, date);
  return r.boundary;
}

// Evaluate a pre-parsed arithmetic formula: [(sign, ref_node, ref_letter), ...]
// Returns null if any term can't be resolved (so the caller can fall back to
// tree-walk or display "—").
function evalFormulaAt(nid, varLetter, date) {
  const terms = FORMULAS[nid] && FORMULAS[nid][varLetter];
  if (!terms || !terms.length) return null;
  let sum = 0;
  for (const [sign, refNode, refLetter] of terms) {
    const v = resolvedValueAt(refNode, refLetter, date);
    if (v === null || v === undefined) return null;
    sum += sign * v;
  }
  return sum;
}

// Sum of a node's direct tree children's resolved values for a variable letter.
//
// For I/O at a parent: when per-edge data is available (n.edges), split the
// child flows into 'boundary' (the other end is OUTSIDE this parent's
// partition — these are real flows in/out of the parent) and 'intra' (the
// other end is ALSO under this parent — PADD2 -> PADD3 inflows are intra-USA
// at usa_view and net out). Returns {boundary, intra} so the caller can show
// both: boundary as the green sum that matches the parent's observation,
// intra as a smaller annotation for situational awareness.
//
// For P/C/S/B the children's resolved values are summed directly — there's no
// concept of "intra-partition" for non-relational variables — and only the
// boundary slot is populated.
function treeSumKids(nid, varLetter, date) {
  // Refactored 2026-05-15: reads pre-computed sums from the payload's
  // per-node `sum_kids` and `intra` dicts (sourced from SQL views
  // v_partition_sums and v_partition_intra_flows). All partition math
  // is done in the resolver pipeline; the JS is lookup-only.
  const n = NODES[nid];
  if (!n) return { boundary: null, intra: null, n_missing: 0, n_total: 0 };
  const sk    = (n.sum_kids && n.sum_kids[varLetter])  ? n.sum_kids[varLetter]  : null;
  const intra = (n.intra    && n.intra[varLetter])     ? n.intra[varLetter]     : null;
  const boundary = (sk    && date in sk)    ? sk[date]    : null;
  const intraVal = (intra && date in intra) ? intra[date] : null;
  return { boundary, intra: intraVal, n_missing: 0, n_total: 0 };
}

// Set of every node_id under `nid` in the partition tree (including nid itself).
// Cached per call across the render — descendants are stable per page load.
const _descCache = {};
function partitionDescendants(nid) {
  if (_descCache[nid]) return _descCache[nid];
  const out = new Set([nid]);
  const stack = [nid];
  while (stack.length) {
    const cur = stack.pop();
    const cn = NODES[cur];
    if (!cn) continue;
    const ch = (cn.children_partition && cn.children_partition.length)
                 ? cn.children_partition
                 : (cn.children || []);
    for (const c of ch) {
      if (!out.has(c)) { out.add(c); stack.push(c); }
    }
  }
  _descCache[nid] = out;
  return out;
}

// Convenience: return just the boundary slot (the value that nets across
// partition boundaries) — that's what the existing dual-cell rendering uses.
function sumKidsAt(nid, varLetter, date) {
  return treeSumKids(nid, varLetter, date).boundary;
}
// Companion helper: intra-partition flow volume at this parent for I/O.
// Same value for I and O (each intra-partition edge appears as an outflow
// on one child and an inflow on another). Null when there's no meaningful
// intra value (P/C/S/B always null; I/O at leaf-ish parents null).
function intraKidsAt(nid, varLetter, date) {
  return treeSumKids(nid, varLetter, date).intra;
}

// Decide whether the observed and sum values are materially different.
// Tolerance: max(1 unit, 1% of parent value).
// Rationale: EIA series are integer-published, so a 1-unit absolute floor
// covers integer-publication rounding on small values; a 1% relative
// cutoff scales for large values (where 1 unit is irrelevant noise).
// Pedro's framing: "if I have a bunch of adds at xxx.x resolution and a
// total value of xxx.x given, the rounding should be consistent with
// that; if too difficult, use 1 unit or 1%, whichever is greater."
function materiallyDifferent(obs, sum) {
  if (obs === null || obs === undefined) return false;
  if (sum === null || sum === undefined) return false;
  const diff = Math.abs(obs - sum);
  const tol = Math.max(1.0, 0.01 * Math.abs(obs));
  return diff > tol;
}

function buildRow(nid, depth, date) {
  const n = NODES[nid];
  const tr = document.createElement("tr");
  tr.dataset.nid = nid;
  const td0 = document.createElement("td");
  td0.className = "node-cell";
  td0.style.paddingLeft = (8 + depth * 16) + "px";
  const hasChildren = (n.children && n.children.length > 0);
  const arrow = hasChildren ? (expanded.has(nid) ? "▼" : "▶") : " ";
  const roleTag = n.role
    ? `<span class="role-tag ${n.role}">${n.role === "balance" ? "bal" : "cstr"}</span>`
    : "";
  td0.innerHTML =
    `<span class="toggle">${arrow}</span>${nid}` +
    `<span class="kind-tag ${n.kind}">${n.kind === "abstract" ? "abs" : "phy"}</span>` +
    `${roleTag}` +
    `<span class="subtype">${n.node_subtype || ""}</span>`;
  td0.onclick = () => {
    if (hasChildren) {
      if (expanded.has(nid)) expanded.delete(nid); else expanded.add(nid);
      renderTable();
    }
  };
  tr.appendChild(td0);
  for (const v of VAR_LETTERS) {
    const td = document.createElement("td");
    const obs = n.values[v] ? n.values[v][date] : null;
    const kidsTree = treeSumKids(nid, v, date);
    const kids = kidsTree.boundary;
    const intra = kidsTree.intra;
    const hasObs   = (obs   !== null && obs   !== undefined);
    const hasKids  = (kids  !== null && kids  !== undefined);
    const hasIntra = (intra !== null && intra !== undefined && Math.abs(intra) >= 0.5);
    const unit = VAR_UNIT[v];
    const intraTag = hasIntra
      ? `<span class="intra">+${fmtNum(Math.abs(intra), unit)} intra</span>`
      : "";

    if (hasObs && hasKids) {
      td.innerHTML =
        `<span class="observed">${fmtNum(obs, unit)}</span>` +
        `<span class="sum-kids">${fmtNum(kids, unit)}</span>` +
        intraTag;
      // Three-state classification per Pedro's spec:
      //   green  (match within tolerance): "has-data"            (no extra class)
      //   red    (all kids have values, mismatch > tolerance):   "has-data mismatch-red"
      //   yellow (some kids missing data, can't verify):         "has-data mismatch-yellow"
      if (!materiallyDifferent(obs, kids)) {
        td.className = "has-data";  // green default
      } else if (kidsTree.n_missing === 0) {
        td.className = "has-data mismatch-red";  // real divergence
      } else {
        td.className = "has-data mismatch-yellow";  // can't verify
      }
    } else if (hasObs) {
      // Single observation, no children to sum
      td.innerHTML = `<span class="observed">${fmtNum(obs, unit)}</span>` + intraTag;
      td.className = "has-data no-obs-or-sum";
    } else if (hasKids) {
      // No direct observation — show boundary sum (teal italic) + intra tag
      td.innerHTML = `<span class="sum-kids">${fmtNum(kids, unit)}</span>` + intraTag;
      td.className = "has-data no-obs";
    } else {
      td.textContent = "—";
      td.className = "missing";
    }
    if (hasObs || hasKids) {
      td.dataset.nid = nid;
      td.dataset.var = v;
      td.onclick = () => plotVar(nid, v, td);
    }
    tr.appendChild(td);
  }
  return tr;
}

function makeSectionRow(text, blurb) {
  const tr = document.createElement("tr");
  tr.className = "section-row";
  const td = document.createElement("td");
  td.colSpan = 8;
  td.innerHTML = `<b>${text}</b>` +
    (blurb ? ` <span class="blurb">&mdash; ${blurb}</span>` : "");
  tr.appendChild(td);
  return tr;
}
function makeSubsectionRow(text, blurb) {
  const tr = document.createElement("tr");
  tr.className = "subsection-row";
  const td = document.createElement("td");
  td.colSpan = 8;
  td.innerHTML = `<b>${text}</b>` +
    (blurb ? `<span class="blurb">${blurb}</span>` : "");
  tr.appendChild(td);
  return tr;
}

function renderTable() {
  const date = datePicker.value;

  // -- Balance partition tree --
  const balTbody = document.getElementById("tbl-body-balance");
  balTbody.innerHTML = "";
  for (const {nid, depth} of visibleFrom(BALANCE_ROOTS)) {
    balTbody.appendChild(buildRow(nid, depth, date));
  }

  // -- Constraints section: a single section header + one subsection per category --
  const cstrTbody = document.getElementById("tbl-body-constraints");
  cstrTbody.innerHTML = "";
  cstrTbody.appendChild(makeSectionRow(
    "Constraint observations",
    "auxiliary observations + boundary nodes. Not summed into the partition; used as additional equations to constrain latents."
  ));
  for (const group of CONSTRAINT_GROUPS) {
    cstrTbody.appendChild(makeSubsectionRow(group.label, group.blurb));
    for (const {nid, depth} of visibleFrom(group.nodes)) {
      cstrTbody.appendChild(buildRow(nid, depth, date));
    }
  }
}

let _selectedRow = null;
function plotVar(nid, v, td) {
  const n = NODES[nid];
  const series = n.values[v] || {};
  const keys = Object.keys(series).sort();
  const ys = keys.map(d => series[d]);
  const unit = VAR_UNIT[v];

  // highlight the selected row
  if (_selectedRow) _selectedRow.classList.remove("selected");
  const tr = td.parentElement;
  tr.classList.add("selected");
  _selectedRow = tr;

  Plotly.newPlot("chart", [{
    x: keys, y: ys, type: "scatter", mode: "lines+markers",
    line: { color: "#1f77b4", width: 1.5 },
    marker: { size: 4 },
    name: `${nid}.${v}`,
  }], {
    title: `${VAR_LABEL[v]} &middot; ${nid}`,
    xaxis: { title: "date", type: "date" },
    yaxis: { title: unit },
    margin: { l: 60, r: 30, t: 60, b: 50 },
    hovermode: "x unified",
  }, { responsive: true });

  const meta = document.getElementById("chart-meta");
  const vals = ys.filter(x => x !== null && x !== undefined);
  const mn = vals.length ? Math.min(...vals) : null;
  const mx = vals.length ? Math.max(...vals) : null;
  const mean = vals.length ? vals.reduce((a,b)=>a+b,0) / vals.length : null;
  meta.innerHTML =
    `node     : ${nid}\n` +
    `variable : ${v} (${VAR_LABEL[v]})\n` +
    `unit     : ${unit}\n` +
    `points   : ${keys.length}\n` +
    `range    : ${keys[0]}  ->  ${keys[keys.length-1]}\n` +
    `min/mean/max : ${mn !== null ? mn.toFixed(1) : "—"} / ` +
    `${mean !== null ? mean.toFixed(1) : "—"} / ` +
    `${mx !== null ? mx.toFixed(1) : "—"}`;
  meta.style.whiteSpace = "pre-wrap";
}

datePicker.addEventListener("change", renderTable);

// Start collapsed (only roots visible)
expanded.clear();
renderTable();
document.getElementById("chart").innerHTML =
  `<div class="placeholder">Click a numeric cell to see its time series.</div>`;
</script>
</body>
</html>
"""


def main():
    print("Querying DB...")
    (assets, variables, parent_via_formula, node_values,
     node_roles, agg_sums, formulas) = fetch()
    payload = build_payload(assets, variables, parent_via_formula, node_values,
                            node_roles, agg_sums, formulas)
    print(f"  nodes: {len(payload['nodes'])}, "
          f"balance roots: {len(payload['balance_roots'])}, "
          f"constraint roots: {len(payload['constraint_roots'])}, "
          f"dates: {len(payload['dates'])}")

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":"), default=float))
    HTML_OUT.write_text(html, encoding="utf-8")
    size_kb = HTML_OUT.stat().st_size // 1024
    print(f"Wrote {HTML_OUT}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
