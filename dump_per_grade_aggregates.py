"""Dump per-grade main-group yield aggregates from refineries.refinery_grade_slate.

Writes a JSON file with one entry per (commodity, main_group) showing
n_refineries, avg / min / max / stddev of group-total yield_pct across all
refineries that process that grade. Used to compare our loaded slates
against independent industry benchmarks.

One-shot; output goes to Stage2 root as audit material.
"""
from __future__ import annotations

import json
from datetime import date

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
OUT = "per_grade_db_aggregates.json"


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        # For each (refinery, commodity), sum leaf yields up to main-group totals.
        # Then aggregate across refineries per (commodity, main_group).
        cur.execute("""
            WITH per_pair AS (
                SELECT
                    commodity,
                    refinery_id,
                    COALESCE(product_parent_code, product_code) AS main_group,
                    SUM(yield_pct) AS group_total
                FROM refineries.v_refinery_grade_slate
                GROUP BY commodity, refinery_id,
                         COALESCE(product_parent_code, product_code)
            )
            SELECT commodity, main_group,
                   COUNT(DISTINCT refinery_id)         AS n_refineries,
                   ROUND(AVG(group_total)::numeric, 2)    AS avg_yield,
                   ROUND(MIN(group_total)::numeric, 2)    AS min_yield,
                   ROUND(MAX(group_total)::numeric, 2)    AS max_yield,
                   ROUND(STDDEV(group_total)::numeric, 2) AS stddev_yield
            FROM per_pair
            GROUP BY commodity, main_group
            ORDER BY commodity, avg_yield DESC
        """)
        rows = cur.fetchall()

    out: dict[str, dict[str, dict]] = {}
    for commodity, main_group, n, avg, mn, mx, sd in rows:
        out.setdefault(commodity, {})[main_group] = {
            "n_refineries": n,
            "avg_yield_pct": float(avg) if avg is not None else None,
            "min_yield_pct": float(mn) if mn is not None else None,
            "max_yield_pct": float(mx) if mx is not None else None,
            "stddev_yield_pct": float(sd) if sd is not None else None,
        }

    payload = {
        "generated_at": date.today().isoformat(),
        "kind": "per_grade_main_group_aggregates",
        "notes": (
            "Aggregated across all refineries processing each grade, rolling "
            "leaf product yields up to their products.oil_products parent. "
            "main_group is the parent_code of the leaf product in "
            "products.oil_products."
        ),
        "n_grades": len(out),
        "grades": out,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"wrote {OUT}  ({len(out)} grades)")


if __name__ == "__main__":
    main()
