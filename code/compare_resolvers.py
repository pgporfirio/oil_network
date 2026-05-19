"""compare_resolvers.py — run the legacy and recursive resolvers back-to-back
and diff their outputs row-by-row.

Workflow:
  1. Run resolve_scenario.resolve() — values live in scenario_resolved_values.
  2. Snapshot scenario_resolved_values to a temp table.
  3. Run recursive_resolver.resolve_recursive() — overwrites the table.
  4. Diff the snapshot vs the current state and report.

Equivalence check (resolver.md §5):
  - SELECT … EXCEPT … both ways on (scenario_id, variable_id, observation_date,
    value, source) should return zero rows.
  - Dispatch counts in scenario_resolver_runs.dispatch_stats should match
    within ±0 for observed, zero, sum, arithmetic, reverse_mirror,
    unresolved. `latent` may differ if any latents were silently absorbing
    cases that should have been mirror — but with Option B's
    promote_mirrors() retained, even this should match.
"""
from __future__ import annotations

import argparse

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

SNAPSHOT_TABLE = "oil_network._compare_legacy_snapshot"


def fmt_n(n):
    return f"{n:>10,}"


def run_compare(scenario_id: str = "starter_us_crude_2015_2025") -> None:
    print("=" * 72)
    print(f"COMPARE RESOLVERS  scenario_id = {scenario_id}")
    print("=" * 72)

    # ---- Step 1: run the legacy resolver ----
    print("\n[1/4] Running legacy resolver (resolve_scenario.resolve)…")
    from resolve_scenario import resolve as legacy_resolve
    legacy_resolve(scenario_id, verbose=False, notes="compare:legacy")
    print("      legacy done.")

    # ---- Step 2: snapshot ----
    print("\n[2/4] Snapshotting scenario_resolved_values…")
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {SNAPSHOT_TABLE}")
        cur.execute(f"""
            CREATE TABLE {SNAPSHOT_TABLE} AS
            SELECT * FROM oil_network.scenario_resolved_values
            WHERE scenario_id = %s
        """, (scenario_id,))
        cur.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}")
        n_legacy = cur.fetchone()[0]
        conn.commit()
        print(f"      snapshot rows: {fmt_n(n_legacy)}")

    # Capture legacy dispatch stats for later compare
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT dispatch_stats, duration_ms FROM oil_network.scenario_resolver_runs
            WHERE scenario_id = %s AND notes = 'compare:legacy'
            ORDER BY started_at DESC LIMIT 1
        """, (scenario_id,))
        legacy_run = cur.fetchone()
    legacy_stats, legacy_dur = (legacy_run if legacy_run else (None, None))

    # ---- Step 3: run recursive ----
    print("\n[3/4] Running recursive resolver (recursive_resolver.resolve_recursive)…")
    from recursive_resolver import resolve_recursive
    resolve_recursive(scenario_id, verbose=False, notes="compare:recursive")
    print("      recursive done.")

    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT dispatch_stats, duration_ms FROM oil_network.scenario_resolver_runs
            WHERE scenario_id = %s AND notes = 'compare:recursive'
            ORDER BY started_at DESC LIMIT 1
        """, (scenario_id,))
        rec_run = cur.fetchone()
    rec_stats, rec_dur = (rec_run if rec_run else (None, None))

    # ---- Step 4: diff ----
    print("\n[4/4] Diffing legacy snapshot vs recursive output…")
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM oil_network.scenario_resolved_values WHERE scenario_id = %s",
                    (scenario_id,))
        n_recursive = cur.fetchone()[0]

        # Rows only in legacy
        cur.execute(f"""
            SELECT COUNT(*) FROM {SNAPSHOT_TABLE} l
            LEFT JOIN oil_network.scenario_resolved_values r
              ON l.scenario_id = r.scenario_id
             AND l.variable_id = r.variable_id
             AND l.observation_date = r.observation_date
            WHERE r.scenario_id IS NULL
        """)
        n_only_legacy = cur.fetchone()[0]

        # Rows only in recursive
        cur.execute(f"""
            SELECT COUNT(*) FROM oil_network.scenario_resolved_values r
            LEFT JOIN {SNAPSHOT_TABLE} l
              ON l.scenario_id = r.scenario_id
             AND l.variable_id = r.variable_id
             AND l.observation_date = r.observation_date
            WHERE l.scenario_id IS NULL AND r.scenario_id = %s
        """, (scenario_id,))
        n_only_recursive = cur.fetchone()[0]

        # Value differs (NULL-safe via IS DISTINCT FROM)
        cur.execute(f"""
            SELECT COUNT(*) FROM {SNAPSHOT_TABLE} l
            JOIN oil_network.scenario_resolved_values r
              ON l.scenario_id = r.scenario_id
             AND l.variable_id = r.variable_id
             AND l.observation_date = r.observation_date
            WHERE l.value IS DISTINCT FROM r.value
        """)
        n_diff_value = cur.fetchone()[0]

        # Source differs
        cur.execute(f"""
            SELECT COUNT(*) FROM {SNAPSHOT_TABLE} l
            JOIN oil_network.scenario_resolved_values r
              ON l.scenario_id = r.scenario_id
             AND l.variable_id = r.variable_id
             AND l.observation_date = r.observation_date
            WHERE l.source != r.source
        """)
        n_diff_source = cur.fetchone()[0]

        # formula_used differs (cosmetic only — record but don't fail on it)
        cur.execute(f"""
            SELECT COUNT(*) FROM {SNAPSHOT_TABLE} l
            JOIN oil_network.scenario_resolved_values r
              ON l.scenario_id = r.scenario_id
             AND l.variable_id = r.variable_id
             AND l.observation_date = r.observation_date
            WHERE l.formula_used IS DISTINCT FROM r.formula_used
        """)
        n_diff_formula = cur.fetchone()[0]

        # ---- Print report ----
        print("\n  Row counts:")
        print(f"    legacy:                 {fmt_n(n_legacy)}")
        print(f"    recursive:              {fmt_n(n_recursive)}")
        print(f"    only in legacy:         {fmt_n(n_only_legacy)}")
        print(f"    only in recursive:      {fmt_n(n_only_recursive)}")

        print("\n  Cell-level diffs (joined on (scenario, variable, date)):")
        print(f"    value differs:          {fmt_n(n_diff_value)}")
        print(f"    source differs:         {fmt_n(n_diff_source)}")
        print(f"    formula_used differs:   {fmt_n(n_diff_formula)}  (cosmetic)")

        # Sample diffs if any
        if n_diff_value > 0 or n_diff_source > 0:
            print("\n  Sample differences (first 12):")
            cur.execute(f"""
                SELECT l.variable_id, l.observation_date,
                       l.value, r.value, l.source, r.source,
                       l.formula_used, r.formula_used
                FROM {SNAPSHOT_TABLE} l
                JOIN oil_network.scenario_resolved_values r
                  ON l.scenario_id = r.scenario_id
                 AND l.variable_id = r.variable_id
                 AND l.observation_date = r.observation_date
                WHERE l.value IS DISTINCT FROM r.value OR l.source != r.source
                LIMIT 12
            """)
            for row in cur.fetchall():
                vid, d, vl, vr, sl, sr, fl, fr = row
                print(f"    {vid[:55]:<55} {d}")
                print(f"        legacy:    value={vl!s:<14} source={sl:<10}  formula={fl}")
                print(f"        recursive: value={vr!s:<14} source={sr:<10}  formula={fr}")

        # Cleanup snapshot
        cur.execute(f"DROP TABLE IF EXISTS {SNAPSHOT_TABLE}")
        conn.commit()

    # Dispatch stats comparison
    print("\n  Dispatch stats:")
    keys = ("observed", "zero", "latent", "sum", "arithmetic",
            "reverse_mirror", "unresolved")
    print(f"    {'key':<18} {'legacy':>10} {'recursive':>10} {'delta':>8}")
    if legacy_stats and rec_stats:
        for k in keys:
            ll = legacy_stats.get(k, 0)
            rr = rec_stats.get(k, 0)
            delta = rr - ll
            mark = "" if delta == 0 else "  <- differs"
            print(f"    {k:<18} {ll:>10} {rr:>10} {delta:>+8}{mark}")
    else:
        print("    (could not retrieve dispatch_stats from one or both runs)")

    # Timing
    print()
    if legacy_dur is not None and rec_dur is not None:
        print(f"  Duration:  legacy {legacy_dur:>5} ms   recursive {rec_dur:>5} ms")

    # ---- Verdict ----
    print()
    print("=" * 72)
    identical = (n_only_legacy == 0 and n_only_recursive == 0
                 and n_diff_value == 0 and n_diff_source == 0)
    if identical:
        print("VERDICT: outputs identical on (variable, date, value, source).  OK")
    else:
        print("VERDICT: differences found.  Investigate above.")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="starter_us_crude_2015_2025")
    args = parser.parse_args()
    run_compare(args.scenario)


if __name__ == "__main__":
    main()
