"""Post-resolution audit. Queries v_resolution_anomalies and prints a summary.

Exit code is always 0 — anomalies are informational, not blocking. A non-zero
count of `severity='high'` rows is worth investigating before claiming the
scenario state is publication-ready.

Anomaly kinds (see claude/DESIGN_PRINCIPLES.md Corollary D-bis):

  negative_observed  [high] — TS-bound row resolved to value < 0 — real bug
                              (EIA does not publish negative P/C/inflow/outflow).
  negative_derived   [low]  — Arithmetic cascade went negative — typically
                              LOCF + heterogeneous-source-horizon mismatch.
  long_locf_run      [low]  — TS gap > 90 days, may signal stale source.
  unresolved         [high] — Broken assignment; resolver couldn't classify.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT severity, anomaly_kind, COUNT(*) FROM oil_network.v_resolution_anomalies
            GROUP BY severity, anomaly_kind
            ORDER BY severity DESC, anomaly_kind
        """)
        rows = cur.fetchall()

    if not rows:
        print("  v_resolution_anomalies: clean (no anomalies of any kind).")
        return

    print("  v_resolution_anomalies summary:")
    print(f"    {'severity':<10}{'anomaly_kind':<24}{'count':>8}")
    n_high = 0
    for sev, kind, n in rows:
        print(f"    {sev:<10}{kind:<24}{n:>8}")
        if sev == "high":
            n_high += n
    if n_high:
        print(f"  -- {n_high} high-severity row(s) — investigate before publication-ready.")
    else:
        print("  -- All anomalies are low-severity (forecast-horizon or expected LOCF artefacts).")


if __name__ == "__main__":
    main()
