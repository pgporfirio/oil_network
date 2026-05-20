"""resolve_scenario.py — evaluate every variable, persist resolved values.

Implements **Axiom 5** of `DESIGN_PRINCIPLES.md`: each `(scenario, variable,
date)` is either observed (TS-bound) or derived (formula-bound), enforced at
the schema level by `variable_assignments`'s `num_nonnulls` CHECK. This
script materialises the result for every variable in every scenario and
writes one row per `(scenario, variable, date)` to
`oil_network.scenario_resolved_values`.

The resolver is a **single dispatcher** over a small vocabulary of formula
kinds. Each row's `source` column records why the value was produced:

    'observed'   — TS lookup
    'zero'       — formula='0' (structural default; pass-through nodes)
    'latent'     — formula='latent()' (declared unobservable; value=NULL)
    'derived'    — formula evaluated successfully (sum/alias/arithmetic/closure/mirror)
    'partial'    — formula tried but some input was NULL; value=NULL with note
    'unresolved' — none of the kinds matched (should be 0 in a healthy run)

**Corollary D mirror promotion.** For relational variables declared `latent()`
on one side of a flow edge, the framework borrows the value from the paired
direction in a post-pass: if `inflow(A → B)` is latent but `outflow(B → A)`
resolved, the inflow inherits the value. The mirror is structural conservation
(every flow has two sides describing the same physical movement); it is not a
new primitive, just a tactical fill-in.
"""
from __future__ import annotations

import argparse
import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as _date, datetime, timezone
from graphlib import TopologicalSorter
from typing import Callable, Optional

import psycopg2
from psycopg2.extras import execute_values, Json

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
-- Audit log of every resolver invocation. Lets you answer 'when was scenario X
-- last resolved?', 'how long did it take?', 'what did each resolution rule
-- fire on?' — without losing that info to the DELETE-then-INSERT pattern of
-- the values table itself.
CREATE TABLE IF NOT EXISTS oil_network.scenario_resolver_runs (
    run_id          BIGSERIAL PRIMARY KEY,
    scenario_id     TEXT        NOT NULL REFERENCES oil_network.scenarios(scenario_id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    duration_ms     INTEGER,
    n_assignments   INTEGER,
    n_rows_written  INTEGER,
    dispatch_stats  JSONB,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS ix_srr_scenario
    ON oil_network.scenario_resolver_runs(scenario_id, started_at DESC);

CREATE TABLE IF NOT EXISTS oil_network.scenario_resolved_values (
    scenario_id      TEXT NOT NULL REFERENCES oil_network.scenarios(scenario_id) ON DELETE CASCADE,
    variable_id      TEXT NOT NULL REFERENCES oil_network.variables(variable_id) ON DELETE CASCADE,
    observation_date DATE NOT NULL,
    value            DOUBLE PRECISION,
    source           TEXT NOT NULL CHECK (source IN
                       ('observed', 'derived', 'zero', 'latent', 'unresolved', 'partial')),
    formula_used     TEXT,
    timeseries_id    TEXT,
    saved_date       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scenario_id, variable_id, observation_date)
);
ALTER TABLE oil_network.scenario_resolved_values
    ADD COLUMN IF NOT EXISTS run_id BIGINT
    REFERENCES oil_network.scenario_resolver_runs(run_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_srv_scenario_date
    ON oil_network.scenario_resolved_values(scenario_id, observation_date);
CREATE INDEX IF NOT EXISTS ix_srv_variable
    ON oil_network.scenario_resolved_values(variable_id);
CREATE INDEX IF NOT EXISTS ix_srv_source
    ON oil_network.scenario_resolved_values(source);
CREATE INDEX IF NOT EXISTS ix_srv_run
    ON oil_network.scenario_resolved_values(run_id);
"""

RE_TERM = re.compile(r'([+\-])?\s*([a-z][a-z0-9_]*)')  # retained for back-compat (unused after AST switch)


# ---------------------------------------------------------------------------
# Safe arithmetic AST (formula language for KIND_ARITHMETIC)
# ---------------------------------------------------------------------------
# Supported: numeric literals, named variable references, +, -, *, /, unary +/-.
# Anything else (function calls, attribute access, subscripts, etc.) is rejected
# at parse time so the formula is unambiguously safe to evaluate.
#
# Example formulas now legal: '0.3 * x + 0.7 * y', '-0.5 * x', 'x', 'x - y - z',
# '(x + y) * 0.62'. Division by zero short-circuits to 0.0 — pragmatic for
# share-style formulas (share of zero is zero, not an error).

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_ALLOWED_UNARYOPS = (ast.USub, ast.UAdd)


def _parse_arithmetic(formula: str) -> Optional[ast.AST]:
    """Parse formula into an expression AST; return None on syntax error or
    on encountering a disallowed node type."""
    try:
        tree = ast.parse(formula.strip(), mode='eval').body
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Name, ast.Constant)):
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARYOPS):
            continue
        if isinstance(node, (ast.Add, ast.Sub, ast.Mult, ast.Div,
                             ast.USub, ast.UAdd, ast.Load)):
            continue  # operator/context leaves
        return None
    # Constant must be numeric (no strings, no booleans-as-numbers)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return None
    return tree


def _arithmetic_names(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _eval_ast(node, get_var: Callable[[str], Optional[float]]) -> tuple[Optional[float], list[str]]:
    """Evaluate an arithmetic AST. Returns (value, missing_names).
    value is None iff any referenced name resolved to None."""
    missing: list[str] = []

    def _e(n):
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            v = get_var(n.id)
            if v is None:
                missing.append(n.id)
                return None
            return v
        if isinstance(n, ast.UnaryOp):
            x = _e(n.operand)
            if x is None: return None
            return -x if isinstance(n.op, ast.USub) else x
        if isinstance(n, ast.BinOp):
            l = _e(n.left); r = _e(n.right)
            if l is None or r is None: return None
            if isinstance(n.op, ast.Add):  return l + r
            if isinstance(n.op, ast.Sub):  return l - r
            if isinstance(n.op, ast.Mult): return l * r
            if isinstance(n.op, ast.Div):  return 0.0 if r == 0 else l / r
        return None  # should not reach (parser rejects disallowed)

    return _e(node), missing


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# One resolution outcome per (variable, date):
#   value         — the numeric result (may be None for latent/partial/unresolved)
#   source        — observed / zero / latent / derived / partial / unresolved
#   formula_used  — short trace of the rule that fired (for the audit table)
#   timeseries_id — only set for source='observed'
Cell = tuple[Optional[float], str, Optional[str], Optional[str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def days_in_month(d: _date) -> int:
    next_m = (d.month % 12) + 1
    next_y = d.year + (1 if d.month == 12 else 0)
    return (_date(next_y, next_m, 1) - _date(d.year, d.month, 1)).days


def shift_months(d: _date, months: int) -> _date:
    """Shift a month-anchored date by N months. months<0 references the past."""
    total = d.year * 12 + (d.month - 1) + months
    return _date(total // 12, (total % 12) + 1, 1)


def paired_variable_id(a: dict) -> Optional[str]:
    """Construct the variable_id of the opposite-direction edge for a relational
    variable. Used by Corollary D's mirror-promotion pass.

    inflow(node A from related B)  <->  outflow(node B to related A)
    """
    if a["variable_type"] not in ("inflow", "outflow") or not a["related_node_id"]:
        return None
    opp = "outflow" if a["variable_type"] == "inflow" else "inflow"
    return f"{opp}__{a['commodity']}__{a['related_node_id']}__{a['node_id']}"


# ---------------------------------------------------------------------------
# Formula classification (Axiom 5 dispatch)
# ---------------------------------------------------------------------------

# The resolver recognises a closed vocabulary of formula kinds. Anything else
# is 'unknown' and resolves to source='unresolved'.
#
# Two earlier kinds were dropped after the principles refactor:
#   - 'alias' (formula = bare variable_id): a single positive-term arithmetic
#     formula. Subsumed into KIND_ARITHMETIC; the audit trail's formula_used
#     becomes the bare variable_id rather than 'alias(X)', which is cosmetic.
#   - 'closure' (B = ΔS - P + C - ΣI + ΣO): dead code since the 6th pass made
#     every balancing_item TS-observed via EIA's MCRUA_* series (B closure
#     count = 0 in every run since). Re-adding it would require a delta()
#     operator on inventory variables — not worth carrying until needed.
KIND_OBSERVED   = "observed"     # TS-bound
KIND_ZERO       = "zero"         # formula = '0'
KIND_LATENT     = "latent"       # formula = 'latent()'
KIND_SUM        = "sum"          # formula = 'sum'   — sugar for +1·every formula_input
KIND_ARITHMETIC = "arithmetic"   # formula = signed combination of variable_ids
                                 #          (also covers single-term aliases)
KIND_UNKNOWN    = "unknown"


def classify(a: dict, by_id: dict) -> str:
    """Decide which evaluation rule applies to assignment `a`."""
    if a["timeseries_id"]:
        return KIND_OBSERVED
    formula = a["formula"] or ""
    if formula == "0":
        return KIND_ZERO
    if formula == "latent()":
        return KIND_LATENT
    if formula == "sum":
        return KIND_SUM
    # Arithmetic: AST parses + every Name is in formula_inputs and known to
    # by_id. Numeric literals (e.g. '0.3 * x') don't need to appear in
    # formula_inputs.
    if formula:
        tree = _parse_arithmetic(formula)
        if tree is not None:
            input_set = set(a["formula_inputs"] or [])
            names = _arithmetic_names(tree)
            if names <= input_set and all(n in by_id for n in names):
                return KIND_ARITHMETIC
    return KIND_UNKNOWN


# ---------------------------------------------------------------------------
# Per-kind evaluators
# ---------------------------------------------------------------------------
# Each evaluator returns a dict {date: Cell} for the variable. Pure functions
# — no side effects, easy to unit-test in isolation.

def eval_observed(a: dict, dates: list[_date],
                  ts_data: dict[str, dict[_date, float]]) -> dict[_date, Cell]:
    """LOCF — last observation carried forward, with audit-trail tagging.

    A monthly EIA value represents either the average daily rate over the
    month (bpd quantities) or the end-of-period stock (mbbl quantities). The
    framework's convention is that the value applies to every subsequent date
    until a new observation lands.

    Fresh observations (the date matches an actual TS data row exactly) get
    `formula_used = NULL`. Carried-forward rows get
    `formula_used = 'locf(YYYY-MM-DD)'` recording the date of the original
    observation, so downstream consumers can distinguish "freshly published
    this month" from "carried over from N months ago" — and a frequency-gap
    audit can flag long LOCF runs.

    Dates BEFORE the first observation get no row (we don't backfill).
    """
    ts_id = a["timeseries_id"]
    series = ts_data.get(ts_id, {})
    if not series:
        return {}
    sorted_obs = sorted(series.items())  # [(date, value), …] ascending
    out: dict[_date, Cell] = {}
    obs_idx = 0
    last_v: Optional[float] = None
    last_d: Optional[_date] = None
    for d in dates:
        while obs_idx < len(sorted_obs) and sorted_obs[obs_idx][0] <= d:
            last_d, last_v = sorted_obs[obs_idx]
            obs_idx += 1
        if last_v is None:
            continue
        if last_d == d:
            out[d] = (last_v, "observed", None, ts_id)               # fresh
        else:
            out[d] = (last_v, "observed", f"locf({last_d})", ts_id)  # carried
    return out


def eval_zero(a: dict, dates: list[_date]) -> dict[_date, Cell]:
    return {d: (0.0, "zero", "0", None) for d in dates}


def eval_latent(a: dict, dates: list[_date]) -> dict[_date, Cell]:
    # Bare latent — value=NULL. The mirror-promotion pass may upgrade some
    # of these rows to source='derived' if the paired direction resolves.
    return {d: (None, "latent", "latent()", None) for d in dates}


def _input_offset_map(a: dict) -> dict[str, int]:
    """Build {input_var -> offset_months} from parallel arrays. Missing or
    short offsets list is treated as all-zero (same-date)."""
    inputs = a["formula_inputs"] or []
    offsets = a.get("formula_input_offsets") or []
    if len(offsets) != len(inputs):
        return {i: 0 for i in inputs}
    return dict(zip(inputs, offsets))


def eval_sum(a: dict, dates: list[_date],
             get: Callable[[str, _date], Optional[float]],
             put: Optional[Callable[[_date, Cell], None]] = None
             ) -> dict[_date, Cell]:
    inputs = a["formula_inputs"] or []
    n_inputs = len(inputs)
    off = _input_offset_map(a)
    out: dict[_date, Cell] = {}
    for d in dates:
        vals = [get(i, shift_months(d, off[i]) if off[i] else d) for i in inputs]
        present = [v for v in vals if v is not None]
        if not present:
            continue
        n_missing = n_inputs - len(present)
        if n_missing == 0:
            cell = (sum(present), "derived", "sum", None)
        else:
            note = f"sum (partial: {n_missing}/{n_inputs} inputs NULL)"
            cell = (sum(present), "partial", note, None)
        out[d] = cell
        if put: put(d, cell)
    return out


def eval_arithmetic(a: dict, dates: list[_date],
                    get: Callable[[str, _date], Optional[float]],
                    put: Optional[Callable[[_date, Cell], None]] = None
                    ) -> dict[_date, Cell]:
    formula = a["formula"]
    off = _input_offset_map(a)
    tree = _parse_arithmetic(formula)
    out: dict[_date, Cell] = {}
    if tree is None:
        # Defensive: classify() should have routed this to KIND_UNKNOWN. If we
        # somehow land here, mark unresolved at every date.
        for d in dates:
            cell = (None, "unresolved", formula[:80], None)
            out[d] = cell
            if put: put(d, cell)
        return out
    for d in dates:
        def get_var(name, _d=d):
            return get(name, shift_months(_d, off.get(name, 0)) if off.get(name, 0) else _d)
        value, missing = _eval_ast(tree, get_var)
        if value is not None and not missing:
            cell = (value, "derived", formula[:80], None)
        else:
            missing_str = ",".join(missing[:3])
            if len(missing) > 3:
                missing_str += f"+{len(missing) - 3}"
            note = f"{formula[:60]} (missing: {missing_str})"
            cell = (None, "partial", note[:100], None)
        out[d] = cell
        if put: put(d, cell)
    return out


def eval_unknown(a: dict, dates: list[_date]) -> dict[_date, Cell]:
    note = (a["formula"] or "")[:80]
    return {d: (None, "unresolved", note, None) for d in dates}


# ---------------------------------------------------------------------------
# Dependency graph for topological evaluation
# ---------------------------------------------------------------------------

def build_deps(assignments: list[dict], by_id: dict) -> dict[str, set[str]]:
    """Each formula-bound variable depends on the variables named in its
    formula_inputs. TS-bound and structural-zero rows have no deps.

    The mirror promotion pass runs AFTER the main loop, so paired-edge
    relationships are not deps here — that simplification removes the
    previous build_deps's cycle-avoidance gating.
    """
    deps: dict[str, set[str]] = defaultdict(set)
    for a in assignments:
        if a["timeseries_id"]:
            continue
        formula = a["formula"] or ""
        if formula in ("", "0", "latent()"):
            continue
        for inp in a["formula_inputs"] or []:
            if inp in by_id and inp != a["variable_id"]:
                deps[a["variable_id"]].add(inp)
    return deps


# ---------------------------------------------------------------------------
# Mirror promotion pass (Corollary D)
# ---------------------------------------------------------------------------

def promote_mirrors(resolved: dict[str, dict[_date, Cell]],
                    by_id: dict) -> int:
    """In-place: latent relational variables borrow values from their paired
    direction if the paired side has a resolved (non-NULL) value at that date.

    Returns the number of variables that got at least one mirror promotion
    (matches the legacy 'reverse_mirror' stat).
    """
    n_promoted = 0
    for vid, by_date in list(resolved.items()):
        a = by_id.get(vid)
        if not a or a["formula"] != "latent()":
            continue
        pvid = paired_variable_id(a)
        if not pvid or pvid not in by_id:
            continue
        paired = resolved.get(pvid, {})
        if not paired:
            continue
        changed = False
        for d, cell in list(by_date.items()):
            if cell[0] is not None:
                continue
            paired_cell = paired.get(d)
            if paired_cell and paired_cell[0] is not None:
                by_date[d] = (paired_cell[0], "derived", f"mirror({pvid})", None)
                changed = True
        if changed:
            n_promoted += 1
    return n_promoted


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load(cur, scenario_id: str):
    """Read assignments + TS data + the unioned date axis."""
    cur.execute("""
        SELECT variable_id, node_id, variable_type, commodity, related_node_id,
               timeseries_id, COALESCE(formula, '') AS formula,
               formula_inputs, formula_input_offsets
        FROM oil_network.v_effective_assignments
        WHERE scenario_id = %s
    """, (scenario_id,))
    cols = [c.name for c in cur.description]
    assignments = [dict(zip(cols, r)) for r in cur.fetchall()]

    ts_ids = sorted({a["timeseries_id"] for a in assignments if a["timeseries_id"]})
    cur.execute("""
        SELECT DISTINCT ON (timeseries_id, observation_date)
               timeseries_id, observation_date, value
        FROM oil_network.timeseries_data
        WHERE timeseries_id = ANY(%s)
        ORDER BY timeseries_id, observation_date, saved_date DESC
    """, (ts_ids,))
    ts_data: dict[str, dict[_date, float]] = defaultdict(dict)
    all_dates: set[_date] = set()
    for ts_id, d, v in cur.fetchall():
        if v is not None:
            ts_data[ts_id][d] = float(v)
        all_dates.add(d)
    return assignments, dict(ts_data), sorted(all_dates)


# ---------------------------------------------------------------------------
# Main resolve loop
# ---------------------------------------------------------------------------

def resolve(scenario_id: str, dry_run: bool = False, verbose: bool = True,
            notes: Optional[str] = None):
    started_at = datetime.now(timezone.utc)
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()

        run_id = None
        if not dry_run:
            cur.execute(
                """
                INSERT INTO oil_network.scenario_resolver_runs
                    (scenario_id, started_at, notes)
                VALUES (%s, %s, %s)
                RETURNING run_id
                """,
                (scenario_id, started_at, notes),
            )
            run_id = cur.fetchone()[0]
            conn.commit()
            if verbose:
                print(f"[run]  audit run_id = {run_id}  started_at = {started_at:%Y-%m-%d %H:%M:%S %Z}")

        if verbose:
            print(f"[load] scenario {scenario_id!r}")
        assignments, ts_data, dates = load(cur, scenario_id)
        if verbose:
            print(f"  assignments: {len(assignments):,}")
            print(f"  timeseries:  {len(ts_data):,}")
            print(f"  dates:       {len(dates):,}  ({dates[0]} .. {dates[-1]})")

        by_id = {a["variable_id"]: a for a in assignments}
        deps = build_deps(assignments, by_id)

        sorter = TopologicalSorter()
        for vid in by_id:
            sorter.add(vid, *deps.get(vid, set()))
        order = list(sorter.static_order())

        resolved: dict[str, dict[_date, Cell]] = defaultdict(dict)
        stats: dict[str, int] = defaultdict(int)
        scenario_start = dates[0] if dates else None

        def get(vid: str, d: _date) -> Optional[float]:
            # Pre-scenario lookups (offsets reach before the first date) seed
            # with 0 — the natural initial condition for cumulative variables
            # like inventory.
            if scenario_start is not None and d < scenario_start:
                return 0.0
            cell = resolved.get(vid, {}).get(d)
            return cell[0] if cell else None

        # --- Main pass: evaluate each variable in topological order ---------
        for vid in order:
            a = by_id.get(vid)
            if not a:
                continue
            kind = classify(a, by_id)
            stats[kind] += 1
            # `put` is the early-register callback: incremental write so that
            # a variable's formula can reference its own past values
            # (offset<0) during this very loop's iteration.
            put = (lambda d, cell, _vid=vid: resolved[_vid].__setitem__(d, cell))
            if kind == KIND_OBSERVED:
                resolved[vid] = eval_observed(a, dates, ts_data)
            elif kind == KIND_ZERO:
                resolved[vid] = eval_zero(a, dates)
            elif kind == KIND_LATENT:
                resolved[vid] = eval_latent(a, dates)
            elif kind == KIND_SUM:
                resolved[vid] = eval_sum(a, dates, get, put)
            elif kind == KIND_ARITHMETIC:
                resolved[vid] = eval_arithmetic(a, dates, get, put)
            else:
                resolved[vid] = eval_unknown(a, dates)

        # --- Mirror promotion pass (Corollary D) ---------------------------
        n_mirror = promote_mirrors(resolved, by_id)
        # Re-label stats: each promoted variable counted as 'latent' above is
        # now meaningfully 'reverse_mirror'. Keep the breakdown legible.
        stats["latent"] -= n_mirror
        stats["reverse_mirror"] = n_mirror

        # Re-map unknown → unresolved in the reported stats (the legacy
        # dispatch_stats schema doesn't have an 'unknown' key)
        stats["unresolved"] = stats.pop(KIND_UNKNOWN, 0)

        # --- Report ---------------------------------------------------------
        if verbose:
            print()
            print("[resolve] dispatch counts:")
            for k in ("observed", "zero", "latent", "sum", "arithmetic",
                      "reverse_mirror", "unresolved"):
                print(f"  {k:22s} {stats.get(k, 0):5d}")

        # --- Persist --------------------------------------------------------
        if dry_run:
            if verbose:
                print()
                print("[dry-run] no rows written")
            return resolved

        if verbose:
            print()
            print(f"[write] clearing {scenario_id!r} ...")
        cur.execute(
            "DELETE FROM oil_network.scenario_resolved_values WHERE scenario_id = %s",
            (scenario_id,),
        )

        rows = [
            (scenario_id, vid, d, v, src, fmla, ts_id, run_id)
            for vid, by_date in resolved.items()
            for d, (v, src, fmla, ts_id) in by_date.items()
        ]
        execute_values(
            cur,
            """
            INSERT INTO oil_network.scenario_resolved_values
                (scenario_id, variable_id, observation_date, value, source,
                 formula_used, timeseries_id, run_id)
            VALUES %s
            """,
            rows,
            page_size=5000,
        )

        completed_at = datetime.now(timezone.utc)
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)
        cur.execute(
            """
            UPDATE oil_network.scenario_resolver_runs
            SET completed_at   = %s,
                duration_ms    = %s,
                n_assignments  = %s,
                n_rows_written = %s,
                dispatch_stats = %s
            WHERE run_id = %s
            """,
            (completed_at, duration_ms, len(assignments), len(rows),
             Json(dict(stats)), run_id),
        )
        conn.commit()
        if verbose:
            print(f"[write] {len(rows):,} rows persisted  (run_id={run_id}, {duration_ms} ms)")

        # Refresh L4 analytic views so downstream consumers see the new state.
        try:
            from refresh_views import refresh_analytic
            refresh_analytic(verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"[refresh] WARNING: analytic-view refresh failed: {e}")

        return resolved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="starter_us_crude_2015_2025")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--notes", default=None,
                   help="Free-text annotation persisted on the audit row")
    args = p.parse_args()
    resolve(args.scenario, dry_run=args.dry_run,
            verbose=not args.quiet, notes=args.notes)


if __name__ == "__main__":
    main()
