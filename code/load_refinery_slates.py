"""Load per-(refinery, grade) yield slates from config/refinery_grade_slates.json.

Mirrors the load_refinery_grades.py pattern: the JSON is authoritative,
the loader is the only writer to refineries.refinery_grade_slate.

JSON shape (one entry per refinery, each carrying per-grade product arrays):

    {
      "refineries": [
        {
          "refinery_id": "ref_exxon_baytown",
          "grades": [
            {"commodity": "mars",
             "products": [
                 {"product_code": "ulsd",     "yield_pct": 36.1,
                  "source": "algorithmic_baseline", "notes": "archetype=deep_conversion"},
                 ...
             ]},
            ...
          ]
        },
        ...
      ]
    }

Validation:
  - Every (refinery_id, commodity) pair must exist in
    refineries.refinery_grade_assignments. Otherwise the composite FK
    on the target table would reject the row.
  - Every product_code must exist in products.oil_products.
  - Yields must be non-negative.

Idempotent. The loader DELETEs slate rows for any (refinery, commodity)
pair mentioned in the JSON before inserting fresh rows. Pairs not in the
JSON are left untouched.

Run via:

    ..\\..\\.venv\\Scripts\\python.exe code\\load_refinery_slates.py
"""
from __future__ import annotations

import json
import sys

import psycopg2
from psycopg2.extras import execute_values

from paths import CONFIG_DIR

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SEED_PATH = CONFIG_DIR / "refinery_grade_slates.json"


def _validate(payload: dict, known_pairs: set[tuple[str, str]],
              known_products: set[str]) -> list[tuple]:
    rows: list[tuple] = []
    unknown_pairs: list[tuple[str, str]] = []
    unknown_products: list[tuple[str, str, str]] = []
    bad_yields: list[tuple[str, str, str, float]] = []
    dup_rows: list[tuple[str, str, str]] = []

    for entry in payload.get("refineries", []):
        rid = entry["refinery_id"]
        for g in entry.get("grades", []):
            comm = g["commodity"]
            if (rid, comm) not in known_pairs:
                unknown_pairs.append((rid, comm))
                continue
            seen_local: set[str] = set()
            for prod in g.get("products", []):
                pcode = prod["product_code"]
                yld   = float(prod["yield_pct"])
                if pcode not in known_products:
                    unknown_products.append((rid, comm, pcode))
                    continue
                if yld < 0:
                    bad_yields.append((rid, comm, pcode, yld))
                    continue
                if pcode in seen_local:
                    dup_rows.append((rid, comm, pcode))
                    continue
                seen_local.add(pcode)
                rows.append((rid, comm, pcode, yld,
                             prod.get("source"), prod.get("notes")))

    fatal = False
    if unknown_pairs:
        print(f"\nERROR — {len(unknown_pairs)} (refinery, commodity) pairs "
              f"not in refinery_grade_assignments:")
        for r, c in unknown_pairs[:10]:
            print(f"    {r} / {c}")
        if len(unknown_pairs) > 10:
            print(f"    ... and {len(unknown_pairs) - 10} more")
        fatal = True
    if unknown_products:
        print(f"\nERROR — {len(unknown_products)} unknown product references:")
        for r, c, p in unknown_products[:10]:
            print(f"    {r} / {c} -> {p}")
        if len(unknown_products) > 10:
            print(f"    ... and {len(unknown_products) - 10} more")
        fatal = True
    if bad_yields:
        print(f"\nERROR — {len(bad_yields)} negative yield values")
        fatal = True
    if dup_rows:
        print(f"\nERROR — {len(dup_rows)} duplicate (refinery, commodity, product) rows")
        fatal = True

    if fatal:
        sys.exit(2)
    return rows


def main() -> None:
    if not SEED_PATH.exists():
        sys.exit(f"ERROR — seed file not found: {SEED_PATH}")

    with open(SEED_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT refinery_id, commodity FROM refineries.refinery_grade_assignments
        """)
        known_pairs = {(r[0], r[1]) for r in cur.fetchall()}
        cur.execute("SELECT product_code FROM products.oil_products")
        known_products = {r[0] for r in cur.fetchall()}

        n_entries = sum(len(r.get("grades", [])) for r in payload.get("refineries", []))
        print(f"[1] validating JSON ({len(payload.get('refineries', []))} refineries, "
              f"{n_entries} (refinery, grade) pairs)")
        rows = _validate(payload, known_pairs, known_products)
        print(f"    -> {len(rows)} product rows validated")

        # Delete-then-insert for pairs mentioned. Composite key (rid, comm).
        mentioned = sorted({(r[0], r[1]) for r in rows})
        if mentioned:
            print(f"[2] deleting existing slate rows for {len(mentioned)} mentioned pairs")
            execute_values(
                cur,
                "DELETE FROM refineries.refinery_grade_slate s "
                "USING (VALUES %s) v(rid, comm) "
                "WHERE s.refinery_id = v.rid AND s.commodity = v.comm",
                mentioned, page_size=500,
            )

        if rows:
            print(f"[3] inserting {len(rows)} fresh rows")
            execute_values(
                cur,
                "INSERT INTO refineries.refinery_grade_slate "
                "(refinery_id, commodity, product_code, yield_pct, source, notes) "
                "VALUES %s",
                rows, page_size=500,
            )

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM refineries.refinery_grade_slate")
        n_total = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(DISTINCT (refinery_id, commodity)),
                   COUNT(DISTINCT refinery_id),
                   COUNT(DISTINCT product_code)
            FROM refineries.refinery_grade_slate
        """)
        n_pairs, n_refineries, n_products = cur.fetchone()

        cur.execute("""
            SELECT COUNT(*) FROM refineries.v_refinery_slate_audit
            WHERE sum_flag IS NOT NULL
        """)
        n_flagged = cur.fetchone()[0]
        cur.execute("""
            SELECT dominant_source, COUNT(*)
            FROM refineries.v_refinery_slate_audit
            GROUP BY dominant_source ORDER BY 2 DESC
        """)
        sources = cur.fetchall()

        print(f"\nFinal state:")
        print(f"  refinery_grade_slate rows:        {n_total}")
        print(f"  distinct (refinery, grade) pairs: {n_pairs}")
        print(f"  distinct refineries:              {n_refineries}")
        print(f"  distinct products in use:         {n_products}")
        print(f"  audit sum-flag mismatches:        {n_flagged}")
        print(f"  dominant source breakdown:")
        for src, n in sources:
            print(f"    {src:<28} {n:>4}")


if __name__ == "__main__":
    main()
