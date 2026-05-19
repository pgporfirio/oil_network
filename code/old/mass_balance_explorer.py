"""Streamlit mass-balance explorer for oil_network.

Drill down from usa_view through PADDs / districts / basins to physical
assets. For a chosen date, every node shows P / C / inflow / outflow / B /
inventory with provenance (TS-bound, derived, latent), and the mass-balance
identity is computed where possible.

Latents are shown explicitly as "?" — the framework treats them as first-class
unobserved quantities, not zero (principle 2.11).

Launch:
    .venv/Scripts/streamlit.exe run Thesis/Code/mass_balance_explorer.py
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
DEFAULT_SCENARIO = "starter_us_crude_2015_2025"


# =============================================================================
# Data fetch (cached for the session)
# =============================================================================

@st.cache_resource
def get_conn():
    return psycopg2.connect(**DB)


def _nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Pandas turns SQL NULL into NaN for text/array cols; map back to None
    so downstream `if x is None` / `isinstance(x, str)` checks behave."""
    return df.astype(object).where(df.notna(), None)


@st.cache_data
def fetch_assets() -> pd.DataFrame:
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.name, a.kind, a.node_class, a.node_subtype,
                   l.padd, l.state, l.country
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        return _nan_to_none(pd.DataFrame(cur.fetchall(), columns=cols))


@st.cache_data
def fetch_variables() -> pd.DataFrame:
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT v.variable_id, v.variable_type, v.commodity, v.node_id,
                   v.related_node_id,
                   va.timeseries_id, va.formula, va.formula_inputs
            FROM oil_network.variables v
            LEFT JOIN oil_network.variable_assignments va
              ON va.variable_id = v.variable_id
             AND va.scenario_id = %s
        """, (DEFAULT_SCENARIO,))
        cols = [c.name for c in cur.description]
        return _nan_to_none(pd.DataFrame(cur.fetchall(), columns=cols))


@st.cache_data
def fetch_ts_meta() -> pd.DataFrame:
    with get_conn().cursor() as cur:
        cur.execute("""SELECT timeseries_id, name, unit, source
                       FROM oil_network.timeseries""")
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


@st.cache_data
def fetch_ts_values_for_date(target_date: date) -> dict[str, float]:
    """Latest-vintage value for every TS at the target date."""
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (timeseries_id)
                   timeseries_id, value
            FROM oil_network.timeseries_data
            WHERE observation_date = %s
            ORDER BY timeseries_id, saved_date DESC
        """, (target_date,))
        return dict(cur.fetchall())


@st.cache_data
def fetch_ts_prev_inventory(target_date: date) -> dict[str, float]:
    """For every TS with unit=mbbl_level, fetch the previous-month value (latest vintage)."""
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (td.timeseries_id)
                   td.timeseries_id, td.value
            FROM oil_network.timeseries_data td
            JOIN oil_network.timeseries t USING (timeseries_id)
            WHERE t.unit = 'mbbl_level'
              AND td.observation_date = (%s::date - interval '1 month')::date
            ORDER BY td.timeseries_id, td.saved_date DESC
        """, (target_date,))
        return dict(cur.fetchall())


@st.cache_data
def fetch_available_dates() -> list[date]:
    with get_conn().cursor() as cur:
        cur.execute("""SELECT DISTINCT observation_date
                       FROM oil_network.timeseries_data
                       ORDER BY observation_date DESC""")
        return [r[0] for r in cur.fetchall()]


@st.cache_data
def fetch_aggregate_balance(target_date: date) -> pd.DataFrame:
    with get_conn().cursor() as cur:
        cur.execute("""SELECT region_node, s_mbbl, ds_kbd, p_kbd, c_kbd,
                              fin_kbd, fout_kbd, b_kbd
                       FROM oil_network.v_aggregate_balance
                       WHERE observation_date = %s""",
                    (target_date,))
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


@st.cache_data
def fetch_aggregation_consistency(target_date: date) -> pd.DataFrame:
    with get_conn().cursor() as cur:
        cur.execute("""SELECT * FROM oil_network.v_aggregation_consistency
                       WHERE observation_date = %s""", (target_date,))
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


# =============================================================================
# Parent-map / tree
# =============================================================================

EXPLICIT_PADD = {
    "padd1_other": "1", "padd2_other": "2", "padd3_other": "3",
    "padd4_other": "4", "padd5_other": "5",
    "texas_state_view": "3", "montana_state_view": "4",
    "permian_nm": "3",
}
ROOT_NODES = {"usa_view", "canadian_oil_sands", "foreign_supply",
              "foreign_export_destination"}


def derive_padd(asset: dict) -> str | None:
    nid = asset["asset_id"]
    if nid in EXPLICIT_PADD:
        return EXPLICIT_PADD[nid]
    p = asset.get("padd")
    if p:
        m = re.search(r"([1-5])", str(p))
        if m:
            return m.group(1)
    m = re.match(r"padd([1-5])_", nid)
    if m:
        return m.group(1)
    m = re.match(r"district_R([1-5])", nid)
    if m:
        return m.group(1)
    if nid.startswith(("district_REC", "district_RAP")):
        return "1"
    return None


@st.cache_data
def build_parent_map() -> tuple[dict[str, tuple[str, str]], dict[str, list[str]]]:
    assets = fetch_assets()
    variables = fetch_variables()
    var_to_node = dict(zip(variables["variable_id"], variables["node_id"]))

    parent: dict[str, tuple[str, str]] = {}

    # (1) formula_inputs aggregation
    for _, v in variables.iterrows():
        inputs = v["formula_inputs"]
        if not isinstance(inputs, list):
            continue
        parent_node = v["node_id"]
        for inp in inputs:
            child_node = var_to_node.get(inp)
            if child_node and child_node != parent_node and child_node not in parent:
                parent[child_node] = (parent_node, "formula_inputs")

    # (2) structural overrides
    structural = {
        "padd1_view": ("usa_view", "structural"),
        "padd2_view": ("usa_view", "structural"),
        "padd3_view": ("usa_view", "structural"),
        "padd4_view": ("usa_view", "structural"),
        "padd5_view": ("usa_view", "structural"),
        "permian": ("usa_view", "structural"),
        "bakken": ("usa_view", "structural"),
        "eagle_ford": ("usa_view", "structural"),
        "usa_lower48_excl_gom_view": ("usa_view", "structural"),
        "spr_total": ("usa_view", "structural"),
        "texas_state_view": ("padd3_view", "structural"),
        "montana_state_view": ("padd4_view", "structural"),
        "padd1_other": ("padd1_view", "structural"),
        "padd2_other": ("padd2_view", "structural"),
        "padd3_other": ("padd3_view", "structural"),
        "padd4_other": ("padd4_view", "structural"),
        "padd5_other": ("padd5_view", "structural"),
        "district_REC_refining_view": ("padd1_view", "structural"),
        "district_RAP_refining_view": ("padd1_view", "structural"),
        "district_R2A_refining_view": ("padd2_view", "structural"),
        "district_R2B_refining_view": ("padd2_view", "structural"),
        "district_R2C_refining_view": ("padd2_view", "structural"),
        "district_R3A_refining_view": ("padd3_view", "structural"),
        "district_R3B_refining_view": ("padd3_view", "structural"),
        "district_R3C_refining_view": ("padd3_view", "structural"),
        "district_R3D_refining_view": ("padd3_view", "structural"),
        "district_R3E_refining_view": ("padd3_view", "structural"),
        "permian_tx": ("permian", "structural"),
        "permian_nm": ("permian", "structural"),
        "bakken_nd": ("bakken", "structural"),
        "bakken_mt": ("bakken", "structural"),
        "eagle_ford_tx": ("eagle_ford", "structural"),
    }
    parent.update(structural)

    # (3) geographic fallback
    for _, a in assets.iterrows():
        nid = a["asset_id"]
        if nid in parent or nid in ROOT_NODES:
            continue
        if a["kind"] != "physical":
            continue
        padd = derive_padd(a.to_dict())
        if padd:
            parent[nid] = (f"padd{padd}_view", "geography")

    children: dict[str, list[str]] = defaultdict(list)
    for child, (par, _) in parent.items():
        children[par].append(child)
    for k in children:
        children[k].sort()

    return parent, dict(children)


# =============================================================================
# Value resolver
# =============================================================================

@dataclass
class Resolved:
    value: float | None
    status: str        # 'observed' | 'derived' | 'zero' | 'latent' | 'no_data' | 'unassigned' | 'error'
    detail: str = ""   # human-readable provenance


class Resolver:
    """Resolves variable values for a single target date.

    Caches every resolved variable for the session. Detects cycles by tracking
    in-flight ids on the call stack.
    """

    def __init__(self, target_date: date):
        self.target_date = target_date
        self.variables = fetch_variables().set_index("variable_id", drop=False)
        self.ts_meta = fetch_ts_meta().set_index("timeseries_id")
        self.ts_values = fetch_ts_values_for_date(target_date)
        self.ts_prev_values = fetch_ts_prev_inventory(target_date)
        self.days_in_month = pd.Period(target_date, freq="M").days_in_month
        self.cache: dict[str, Resolved] = {}
        self._inflight: set[str] = set()

    def delta_inventory_kbd(self, inv_variable_id: str) -> Resolved:
        """ΔS in kbd for an inventory variable. Requires both current and
        previous month observations on the same TS. mbbl is already
        thousand-barrels, so mbbl/day = kbd directly (no ×1000).
        """
        if inv_variable_id not in self.variables.index:
            return Resolved(None, "error", "inventory variable not found")
        row = self.variables.loc[inv_variable_id]
        ts_id = row["timeseries_id"]
        if not ts_id or not isinstance(ts_id, str):
            return Resolved(None, "no_data", "inventory not TS-bound")
        s_now = self.ts_values.get(ts_id)
        s_prev = self.ts_prev_values.get(ts_id)
        if s_now is None or s_prev is None:
            return Resolved(None, "no_data",
                            f"inventory current or previous month missing ({ts_id})")
        ds_kbd = (s_now - s_prev) / self.days_in_month
        return Resolved(ds_kbd, "derived",
                        f"(S_t − S_t-1) / {self.days_in_month}  [from {ts_id}]")

    def ts_unit(self, ts_id: str) -> str:
        if ts_id in self.ts_meta.index:
            return self.ts_meta.at[ts_id, "unit"]
        return ""

    def resolve(self, variable_id: str) -> Resolved:
        if variable_id in self.cache:
            return self.cache[variable_id]
        if variable_id in self._inflight:
            r = Resolved(None, "error", "cycle in formula_inputs")
            self.cache[variable_id] = r
            return r

        self._inflight.add(variable_id)
        try:
            r = self._resolve_inner(variable_id)
        finally:
            self._inflight.discard(variable_id)

        self.cache[variable_id] = r
        return r

    def _resolve_inner(self, variable_id: str) -> Resolved:
        if variable_id not in self.variables.index:
            return Resolved(None, "error", f"variable {variable_id} not found")

        row = self.variables.loc[variable_id]
        ts_id = row["timeseries_id"]
        formula = row["formula"]
        inputs_raw = row["formula_inputs"]
        inputs = inputs_raw if isinstance(inputs_raw, list) else []
        # Strings from Postgres TEXT come back as str; NULL comes as None or NaN.
        if isinstance(ts_id, float):  # NaN
            ts_id = None
        if isinstance(formula, float):  # NaN
            formula = None

        # TS-bound: direct lookup
        if ts_id:
            val = self.ts_values.get(ts_id)
            if val is None:
                return Resolved(None, "no_data",
                                f"TS {ts_id} has no value at {self.target_date}")
            return Resolved(float(val), "observed", f"TS {ts_id}")

        # No assignment at all
        if formula is None:
            return Resolved(None, "unassigned", "no assignment row")

        formula_s = str(formula).strip()

        # Zero
        if formula_s == "0":
            return Resolved(0.0, "zero", "zero by construction")

        # Latent
        if formula_s == "latent()":
            return Resolved(None, "latent", "latent (unobserved)")

        # sum_over_children: sum formula_inputs, propagate latent if any
        if formula_s == "sum_over_children":
            return self._sum_inputs(inputs, "sum_over_children")

        # sum_over_outflows: discover outflow variables FROM this node
        if formula_s == "sum_over_outflows":
            node_id = row["node_id"]
            outflows = self.variables[
                (self.variables["node_id"] == node_id) &
                (self.variables["variable_type"] == "outflow")
            ]["variable_id"].tolist()
            return self._sum_inputs(outflows, "sum_over_outflows")

        # Region balancing_item pseudo-formula → compute closure from primitives
        if "delta(inventory)" in formula_s:
            return self._region_balancing_item(row["node_id"])

        # Single-variable mirror or passthrough
        if len(inputs) == 1 and formula_s == inputs[0]:
            r = self.resolve(inputs[0])
            return Resolved(r.value, "derived" if r.value is not None else r.status,
                            f"= {inputs[0]}")

        # Arithmetic: substitute each input id with its value, ast-eval
        if any(op in formula_s for op in "+-*/"):
            return self._eval_arithmetic(formula_s, inputs)

        return Resolved(None, "error", f"unhandled formula: {formula_s[:60]}")

    def _sum_inputs(self, inputs: list[str], label: str) -> Resolved:
        if not inputs:
            return Resolved(0.0, "derived", f"{label} (no inputs)")
        total = 0.0
        any_latent = False
        any_unbound = False
        for inp in inputs:
            r = self.resolve(inp)
            if r.value is None:
                if r.status == "latent":
                    any_latent = True
                else:
                    any_unbound = True
            else:
                total += r.value
        if any_latent:
            return Resolved(None, "latent",
                            f"{label} — latent input(s)")
        if any_unbound:
            return Resolved(None, "no_data",
                            f"{label} — missing input(s)")
        return Resolved(total, "derived", f"{label} ({len(inputs)} terms)")

    def _eval_arithmetic(self, formula: str, inputs: list[str]) -> Resolved:
        sub_vals: dict[str, float] = {}
        any_latent = False
        for inp in inputs:
            r = self.resolve(inp)
            if r.value is None:
                if r.status == "latent":
                    any_latent = True
                else:
                    return Resolved(None, "no_data",
                                    f"input {inp} unresolved ({r.status})")
            else:
                sub_vals[inp] = r.value
        if any_latent:
            return Resolved(None, "latent", "arithmetic — latent input")

        # Sort inputs longest-first so substring inputs don't break the rewrite.
        ordered = sorted(inputs, key=len, reverse=True)
        expr = formula
        for i, inp in enumerate(ordered):
            expr = expr.replace(inp, f"__v{i}__")
        env = {f"__v{i}__": sub_vals[inp] for i, inp in enumerate(ordered)}

        try:
            tree = ast.parse(expr, mode="eval")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp,
                                          ast.Name, ast.Constant, ast.Load,
                                          ast.Add, ast.Sub, ast.Mult, ast.Div,
                                          ast.USub, ast.UAdd)):
                    return Resolved(None, "error",
                                    f"disallowed expr node {type(node).__name__}")
            val = eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, env)
            return Resolved(float(val), "derived", f"= {formula}")
        except Exception as e:  # noqa: BLE001
            return Resolved(None, "error", f"eval failed: {e}")

    def _region_balancing_item(self, node_id: str) -> Resolved:
        """Compute B by closure: B = ΔS − P + C − ΣF_in + ΣF_out (all kbd).

        Walks the variables collection on the node directly so it works for
        usa_view (not in v_aggregate_balance) as well as the 5 PADDs.
        """
        on_node = self.variables[self.variables["node_id"] == node_id]
        inv_rows = on_node[on_node["variable_type"] == "inventory"]
        if inv_rows.empty:
            return Resolved(None, "no_data", "no inventory variable on node")
        ds = self.delta_inventory_kbd(inv_rows.iloc[0]["variable_id"])
        if ds.value is None:
            return Resolved(None, ds.status, f"ΔS unresolved: {ds.detail}")

        terms: list[tuple[float, str]] = [(ds.value, "ΔS")]

        def _add(vt: str, sign: float, label: str):
            for _, r in on_node[on_node["variable_type"] == vt].iterrows():
                res = self.resolve(r["variable_id"])
                if res.value is None:
                    return res  # propagate latent / no_data
                terms.append((sign * res.value, f"{label}:{r['variable_id']}"))
            return None

        for vt, sign, label in [("production", -1.0, "P"),
                                  ("consumption", +1.0, "C"),
                                  ("inflow", -1.0, "F_in"),
                                  ("outflow", +1.0, "F_out")]:
            bad = _add(vt, sign, label)
            if bad is not None:
                return Resolved(None, bad.status,
                                f"closure blocked by {label}: {bad.detail}")

        return Resolved(sum(v for v, _ in terms), "derived",
                        "closure: ΔS − P + C − ΣF_in + ΣF_out")


# =============================================================================
# Mass-balance computation per node
# =============================================================================

@dataclass
class MBResult:
    p: Resolved
    c: Resolved
    sum_fin: Resolved
    sum_fout: Resolved
    b: Resolved
    s_now: Resolved
    s_prev: Resolved | None
    ds_kbd: Resolved | None
    implied_ds_kbd: Resolved
    fin_breakdown: list[tuple[str, Resolved]]  # (related_node, resolved)
    fout_breakdown: list[tuple[str, Resolved]]


def compute_mass_balance(node_id: str, target_date: date, resolver: Resolver) -> MBResult:
    vars_df = resolver.variables
    node_vars = vars_df[vars_df["node_id"] == node_id]

    def pick(vt):
        rows = node_vars[node_vars["variable_type"] == vt]
        if rows.empty:
            return None
        return rows.iloc[0]["variable_id"]

    p_id = pick("production")
    c_id = pick("consumption")
    i_id = pick("inventory")
    b_id = pick("balancing_item")

    p = resolver.resolve(p_id) if p_id else Resolved(None, "unassigned", "no P var")
    c = resolver.resolve(c_id) if c_id else Resolved(None, "unassigned", "no C var")
    b = resolver.resolve(b_id) if b_id else Resolved(None, "unassigned", "no B var")
    s_now = resolver.resolve(i_id) if i_id else Resolved(None, "unassigned", "no I var")

    # Inflow / outflow breakdown
    fin_rows = node_vars[node_vars["variable_type"] == "inflow"]
    fout_rows = node_vars[node_vars["variable_type"] == "outflow"]
    fin_breakdown = [
        (r["related_node_id"] or "(none)", resolver.resolve(r["variable_id"]))
        for _, r in fin_rows.iterrows()
    ]
    fout_breakdown = [
        (r["related_node_id"] or "(none)", resolver.resolve(r["variable_id"]))
        for _, r in fout_rows.iterrows()
    ]

    def sum_resolveds(items):
        total = 0.0
        any_latent = False
        any_unbound = False
        for _, r in items:
            if r.value is None:
                if r.status == "latent":
                    any_latent = True
                else:
                    any_unbound = True
            else:
                total += r.value
        if any_latent and not any_unbound:
            return Resolved(None, "latent", f"{len(items)} terms, latent present")
        if any_unbound:
            return Resolved(None, "no_data", f"{len(items)} terms, unbound present")
        return Resolved(total, "derived", f"sum of {len(items)} terms")

    sum_fin = sum_resolveds(fin_breakdown)
    sum_fout = sum_resolveds(fout_breakdown)

    # ΔS in kbd via resolver (mbbl is already thousand barrels → kbd directly).
    s_prev = None
    ds_kbd: Resolved | None = None
    if i_id is not None:
        ds_kbd = resolver.delta_inventory_kbd(i_id)
        ts_id = vars_df.loc[i_id, "timeseries_id"]
        if ts_id:
            prev_raw = resolver.ts_prev_values.get(ts_id)
            if prev_raw is not None:
                s_prev = Resolved(float(prev_raw), "observed",
                                  f"prev-month {ts_id}")

    # Implied ΔS in kbd = P − C + ΣF_in − ΣF_out + B
    parts = [p, c, sum_fin, sum_fout, b]
    if any(x.status == "latent" for x in parts):
        implied = Resolved(None, "latent", "latent component(s)")
    elif any(x.value is None for x in parts):
        implied = Resolved(None, "no_data", "unbound component(s)")
    else:
        implied = Resolved(p.value - c.value + sum_fin.value - sum_fout.value + b.value,
                           "derived", "P − C + ΣF_in − ΣF_out + B")

    return MBResult(p, c, sum_fin, sum_fout, b, s_now, s_prev, ds_kbd, implied,
                    fin_breakdown, fout_breakdown)


# =============================================================================
# UI helpers
# =============================================================================

STATUS_GLYPH = {
    "observed":   "🟢",
    "derived":    "🔵",
    "zero":       "⚪",
    "latent":     "🟡",
    "no_data":    "⚠️",
    "unassigned": "⚫",
    "error":      "🔴",
}

STATUS_LABEL = {
    "observed":   "observed (TS)",
    "derived":    "derived",
    "zero":       "zero by construction",
    "latent":     "latent (unobserved)",
    "no_data":    "no data at this date",
    "unassigned": "no assignment",
    "error":      "evaluation error",
}


def fmt_value(r: Resolved, unit: str = "kbd") -> str:
    if r.value is None:
        return f"{STATUS_GLYPH[r.status]} {r.status}"
    if abs(r.value) >= 1000:
        s = f"{r.value:,.0f}"
    elif abs(r.value) >= 1:
        s = f"{r.value:,.1f}"
    else:
        s = f"{r.value:.3f}"
    return f"{STATUS_GLYPH[r.status]} {s}"


# =============================================================================
# Streamlit app
# =============================================================================

st.set_page_config(page_title="oil_network — mass balance",
                    layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
  .main .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
  .mb-metric { font-family: ui-monospace, Menlo, monospace; font-size: 13px; }
  .legend { font-size: 11.5px; color: #666; }
  h1, h2, h3 { font-family: -apple-system, "Segoe UI", sans-serif; }
  h1 { font-size: 22px !important; }
  h2 { font-size: 16px !important; }
  h3 { font-size: 14px !important; }
  div[data-testid="stMetricValue"] { font-family: ui-monospace, Menlo, monospace; }
</style>
""", unsafe_allow_html=True)

# Load core data
assets = fetch_assets()
variables = fetch_variables()
parent_map, children_map = build_parent_map()
available_dates = fetch_available_dates()

# Sidebar: date + node selection
with st.sidebar:
    st.markdown("### Date")
    default_idx = 0
    for i, d in enumerate(available_dates):
        if d.year == 2024 and d.month == 12:
            default_idx = i
            break
    selected_date = st.selectbox("Observation date",
                                  available_dates,
                                  index=default_idx,
                                  format_func=lambda d: d.strftime("%Y-%m"))

    st.markdown("---")
    st.markdown("### Node")
    if "selected_node" not in st.session_state:
        st.session_state.selected_node = "usa_view"

    # Quick-jump dropdown
    all_nodes = sorted(assets["asset_id"].tolist())
    jump = st.selectbox("Jump to node",
                        ["—"] + all_nodes,
                        index=0,
                        key="jump_select")
    if jump != "—":
        st.session_state.selected_node = jump

    st.markdown("**Roots:**")
    for root in ["usa_view", "canadian_oil_sands", "foreign_supply",
                  "foreign_export_destination"]:
        if root in assets["asset_id"].values:
            if st.button(root, key=f"root_{root}", use_container_width=True):
                st.session_state.selected_node = root

    st.markdown("---")
    st.markdown("<div class='legend'>"
                "🟢 observed · 🔵 derived · ⚪ zero · 🟡 latent · "
                "⚠️ no data · ⚫ unassigned"
                "</div>", unsafe_allow_html=True)

selected_node = st.session_state.selected_node

# Resolver lifetime = (session, date). Refresh when date changes.
if "resolver_date" not in st.session_state or st.session_state.resolver_date != selected_date:
    st.session_state.resolver = Resolver(selected_date)
    st.session_state.resolver_date = selected_date
resolver: Resolver = st.session_state.resolver

# =============================================================================
# Header / breadcrumb
# =============================================================================

asset_row = assets[assets["asset_id"] == selected_node]
if asset_row.empty:
    st.error(f"Node `{selected_node}` not in oil_network.assets")
    st.stop()
asset = asset_row.iloc[0].to_dict()

# Breadcrumb up to root
crumbs: list[str] = []
cur = selected_node
while True:
    crumbs.append(cur)
    p = parent_map.get(cur)
    if not p:
        break
    cur = p[0]
crumbs.reverse()

crumb_html = " <span style='color:#999'>›</span> ".join(
    [(f"<a href='?node={c}' style='text-decoration:none'>{c}</a>"
       if c != selected_node else f"<b>{c}</b>")
     for c in crumbs]
)
st.markdown(f"<div style='font-family:ui-monospace,Menlo,monospace;"
            f"font-size:12px;color:#444'>{crumb_html}</div>",
            unsafe_allow_html=True)

# Title row
left, right = st.columns([3, 2])
with left:
    st.title(f"`{selected_node}` · {selected_date.strftime('%Y-%m')}")
    st.caption(f"{asset.get('name') or ''} — "
               f"{asset['kind']} / {asset['node_class']} / {asset['node_subtype']}")
with right:
    def _s(v):
        return v if isinstance(v, str) and v else None
    geo_bits = []
    if _s(asset.get("padd")):
        geo_bits.append(f"PADD {asset['padd']}")
    if _s(asset.get("state")):
        geo_bits.append(asset["state"])
    if _s(asset.get("country")) and asset["country"] != "US":
        geo_bits.append(asset["country"])
    st.markdown(f"<div style='text-align:right;color:#666;padding-top:8px'>"
                f"{' · '.join(geo_bits) or 'no geography'}</div>",
                unsafe_allow_html=True)

# =============================================================================
# Mass balance panel
# =============================================================================

mb = compute_mass_balance(selected_node, selected_date, resolver)

st.markdown("### Mass balance")
mb_cols = st.columns(6)
mb_cols[0].metric("P", fmt_value(mb.p))
mb_cols[1].metric("C", fmt_value(mb.c))
mb_cols[2].metric("Σ Inflows", fmt_value(mb.sum_fin))
mb_cols[3].metric("Σ Outflows", fmt_value(mb.sum_fout))
mb_cols[4].metric("B", fmt_value(mb.b))

# inventory column
if mb.ds_kbd is not None:
    mb_cols[5].metric("ΔS (kbd)", fmt_value(mb.ds_kbd))
elif mb.s_now.value is not None:
    mb_cols[5].metric("S (mbbl)", fmt_value(mb.s_now, "mbbl"))
else:
    mb_cols[5].metric("ΔS", fmt_value(mb.s_now))

# Identity row
st.markdown("**Identity:** `ΔS = P − C + Σ F_in − Σ F_out + B`  (all in kbd)")

identity_cols = st.columns(3)
identity_cols[0].metric("Implied ΔS  (kbd)", fmt_value(mb.implied_ds_kbd))
identity_cols[1].metric("Observed ΔS  (kbd)",
                         fmt_value(mb.ds_kbd) if mb.ds_kbd else "— no inventory TS")
if (mb.implied_ds_kbd.value is not None and mb.ds_kbd
        and mb.ds_kbd.value is not None):
    gap = mb.ds_kbd.value - mb.implied_ds_kbd.value
    flag = "✓ closes" if abs(gap) < 0.5 else "⚠ gap"
    identity_cols[2].metric("Residual (observed − implied)",
                             f"{gap:+.2f} kbd  {flag}")
else:
    identity_cols[2].metric("Residual", "— partial (latent / unbound)")

# Inflow / outflow breakdown
if mb.fin_breakdown or mb.fout_breakdown:
    with st.expander("Inflow / outflow breakdown", expanded=False):
        bc1, bc2 = st.columns(2)
        with bc1:
            st.markdown("**Inflows**")
            if mb.fin_breakdown:
                rows = [{"from": rel,
                         "value": fmt_value(r),
                         "provenance": r.detail}
                        for rel, r in mb.fin_breakdown]
                st.dataframe(pd.DataFrame(rows), hide_index=True,
                             use_container_width=True)
            else:
                st.caption("no inflow variables")
        with bc2:
            st.markdown("**Outflows**")
            if mb.fout_breakdown:
                rows = [{"to": rel,
                         "value": fmt_value(r),
                         "provenance": r.detail}
                        for rel, r in mb.fout_breakdown]
                st.dataframe(pd.DataFrame(rows), hide_index=True,
                             use_container_width=True)
            else:
                st.caption("no outflow variables")

# =============================================================================
# Children & drill-down
# =============================================================================

st.markdown("### Drill down")

children = children_map.get(selected_node, [])
parent_info = parent_map.get(selected_node)

if parent_info:
    if st.button(f"⬆  back to `{parent_info[0]}`", key="back"):
        st.session_state.selected_node = parent_info[0]
        st.rerun()

if not children:
    st.info("This node has no children — it's a leaf in the aggregation hierarchy.")
else:
    # Aggregation consistency for selected aggregate. v_aggregation_consistency
    # keys on parent_var (a variable_id); look up the variables this node owns.
    consistency_df = fetch_aggregation_consistency(selected_date)
    this_node_var_ids = set(
        variables[variables["node_id"] == selected_node]["variable_id"]
    )
    consistency_row = consistency_df[
        consistency_df["parent_var"].isin(this_node_var_ids)
    ] if "parent_var" in consistency_df.columns else pd.DataFrame()

    if not consistency_row.empty:
        with st.expander("Aggregation consistency (TS observed vs Σ constituents)",
                          expanded=False):
            st.dataframe(consistency_row, hide_index=True,
                         use_container_width=True)

    # Build a per-child summary row at the chosen date
    child_rows = []
    for child in children:
        cmb = compute_mass_balance(child, selected_date, resolver)
        child_rows.append({
            "node":      child,
            "P":         fmt_value(cmb.p),
            "C":         fmt_value(cmb.c),
            "Σ F_in":    fmt_value(cmb.sum_fin),
            "Σ F_out":   fmt_value(cmb.sum_fout),
            "B":         fmt_value(cmb.b),
            "ΔS_implied": fmt_value(cmb.implied_ds_kbd),
            "n_children": len(children_map.get(child, [])),
        })
    children_df = pd.DataFrame(child_rows)

    st.caption(f"{len(children)} children — click a row to drill in.")
    # Streamlit's dataframe doesn't emit row-click events; provide a select.
    pick = st.selectbox("Drill into child node",
                         ["—"] + children,
                         key=f"child_pick_{selected_node}")
    st.dataframe(children_df, hide_index=True, use_container_width=True)
    if pick != "—":
        st.session_state.selected_node = pick
        st.rerun()

# =============================================================================
# Raw variables on the selected node
# =============================================================================

with st.expander("All variables on this node (raw)", expanded=False):
    node_vars = variables[variables["node_id"] == selected_node].copy()
    rows = []
    for _, v in node_vars.iterrows():
        r = resolver.resolve(v["variable_id"])
        unit = resolver.ts_unit(v["timeseries_id"]) if v["timeseries_id"] else "kbd"
        rows.append({
            "type":         v["variable_type"],
            "related":      v["related_node_id"] or "",
            "value":        fmt_value(r),
            "unit":         unit,
            "binding":      ("ts:" + v["timeseries_id"]) if v["timeseries_id"]
                            else (v["formula"] or "(unassigned)"),
            "provenance":   r.detail,
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
    else:
        st.caption("no variables on this node")

# Handle ?node= query param for breadcrumb links
qp = st.query_params
if "node" in qp and qp["node"] != selected_node:
    st.session_state.selected_node = qp["node"]
    del st.query_params["node"]
    st.rerun()
