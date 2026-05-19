"""recursive_resolver.py — fixed-point iteration version of the resolver.

Implements the design from `code/resolver.md`. Replaces `resolve_scenario.py`'s
topological-sort + mirror-post-pass with a single fixed-point loop that sweeps
the unresolved set until nothing new gets resolved.

Migration path (Option B from resolver.md §5): the main evaluation loop is the
new fixed-point loop, but `promote_mirrors()` is still called as a post-pass.
That keeps the data-shape migration (latent → mirror) deferred and lets us
verify equivalence in two steps:

  1. (this file) prove fixed-point produces identical output to topo + mirror.
  2. (later)     migrate latent-with-paired-direction rows to formula='mirror'
                 and inline mirror as a first-class formula kind in the loop.

Same DB writes as resolve_scenario.py: one row per (scenario, variable, date)
to `scenario_resolved_values`, one row per run to `scenario_resolver_runs`,
with the same `dispatch_stats` JSONB schema.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone, date as _date
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values, Json

# Reuse the building blocks. The new loop only changes HOW evaluators are
# called, not what they do; importing keeps the two resolvers locked to the
# same Cell shape, same classify() rules, same eval_* semantics.
from resolve_scenario import (
    DB, DDL, Cell,
    KIND_OBSERVED, KIND_ZERO, KIND_LATENT, KIND_SUM, KIND_ARITHMETIC, KIND_UNKNOWN,
    classify, eval_observed, eval_zero, eval_latent, eval_sum, eval_arithmetic,
    eval_unknown, promote_mirrors, load,
)


def resolve_recursive(scenario_id: str, dry_run: bool = False,
                       verbose: bool = True, notes: Optional[str] = None,
                       max_passes: int = 50):
    """Fixed-point analogue of resolve_scenario.resolve(). Identical
    persistence, audit-trail, and view-refresh behaviour."""
    started_at = datetime.now(timezone.utc)
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()

        run_id = None
        if not dry_run:
            cur.execute(
                """INSERT INTO oil_network.scenario_resolver_runs
                   (scenario_id, started_at, notes) VALUES (%s, %s, %s)
                   RETURNING run_id""",
                (scenario_id, started_at, notes or "recursive_resolver"),
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
        resolved: dict[str, dict[_date, Cell]] = {}
        stats: dict[str, int] = defaultdict(int)

        def get(vid: str, d: _date) -> Optional[float]:
            cell = resolved.get(vid, {}).get(d)
            return cell[0] if cell else None

        def all_inputs_known(a: dict) -> bool:
            inputs = a["formula_inputs"] or []
            return all(inp in resolved for inp in inputs)

        unresolved = set(by_id.keys())

        # ---- Single fixed-point loop ----
        # Every variable kind is handled in the same loop. The three terminal
        # kinds (OBSERVED, ZERO, LATENT) have no formula_inputs, so they're
        # always evaluable — they resolve in the first pass. SUM and
        # ARITHMETIC gate on all_inputs_known and resolve in subsequent passes
        # as their dependencies fill in. The loop stops when no new variable
        # gets resolved in a full sweep.
        n_passes = 0
        while True:
            n_passes += 1
            if n_passes > max_passes:
                if verbose:
                    print(f"[warn] fixed-point did not converge within {max_passes} passes")
                break
            progress = False
            for vid in list(unresolved):
                a = by_id[vid]
                kind = classify(a, by_id)
                # Terminal kinds — no deps, always evaluable
                if kind == KIND_OBSERVED:
                    resolved[vid] = eval_observed(a, dates, ts_data)
                    stats["observed"] += 1
                elif kind == KIND_ZERO:
                    resolved[vid] = eval_zero(a, dates)
                    stats["zero"] += 1
                elif kind == KIND_LATENT:
                    resolved[vid] = eval_latent(a, dates)
                    stats["latent"] += 1
                # Formula kinds — evaluable only when every input is in `resolved`
                elif kind == KIND_SUM and all_inputs_known(a):
                    resolved[vid] = eval_sum(a, dates, get)
                    stats["sum"] += 1
                elif kind == KIND_ARITHMETIC and all_inputs_known(a):
                    resolved[vid] = eval_arithmetic(a, dates, get)
                    stats["arithmetic"] += 1
                else:
                    continue  # not yet resolvable (or KIND_UNKNOWN — handled below)
                unresolved.discard(vid)
                progress = True
            if not progress:
                break

        if verbose:
            print(f"[fixed-point] converged in {n_passes} pass(es)")

        # ---- Anything left after convergence is genuinely UNKNOWN ----
        # In a healthy run this is empty.
        for vid in list(unresolved):
            resolved[vid] = eval_unknown(by_id[vid], dates)
            stats[KIND_UNKNOWN] += 1
            unresolved.discard(vid)

        # ---- Mirror promotion pass (Option B: same as resolve_scenario) ----
        n_mirror = promote_mirrors(resolved, by_id)
        stats["latent"] -= n_mirror
        stats["reverse_mirror"] = n_mirror
        stats["unresolved"] = stats.pop(KIND_UNKNOWN, 0)

        if verbose:
            print()
            print("[resolve] dispatch counts:")
            for k in ("observed", "zero", "latent", "sum", "arithmetic",
                      "reverse_mirror", "unresolved"):
                print(f"  {k:22s} {stats.get(k, 0):5d}")

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
            """INSERT INTO oil_network.scenario_resolved_values
               (scenario_id, variable_id, observation_date, value, source,
                formula_used, timeseries_id, run_id) VALUES %s""",
            rows, page_size=5000,
        )

        completed_at = datetime.now(timezone.utc)
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)
        cur.execute(
            """UPDATE oil_network.scenario_resolver_runs SET
               completed_at = %s, duration_ms = %s,
               n_assignments = %s, n_rows_written = %s,
               dispatch_stats = %s WHERE run_id = %s""",
            (completed_at, duration_ms, len(assignments), len(rows),
             Json(dict(stats)), run_id),
        )
        conn.commit()
        if verbose:
            print(f"[write] {len(rows):,} rows persisted  (run_id={run_id}, {duration_ms} ms)")

        try:
            from refresh_views import refresh_analytic
            refresh_analytic(verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"[refresh] WARNING: analytic-view refresh failed: {e}")

        return resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default="starter_us_crude_2015_2025")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--notes", default=None)
    parser.add_argument("--max-passes", type=int, default=50,
                        help="safety cap on fixed-point iterations")
    args = parser.parse_args()
    resolve_recursive(args.scenario, dry_run=args.dry_run, verbose=not args.quiet,
                       notes=args.notes, max_passes=args.max_passes)


if __name__ == "__main__":
    main()
