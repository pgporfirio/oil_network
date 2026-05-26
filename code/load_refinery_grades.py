"""Load refinery -> crude-grade assignments from config/refinery_grades.json.

The seed file mirrors the asset_graph.json pattern: a JSON document is the
authoritative input, the loader is the only thing that writes to the
target table. Re-running replaces the assignments for any refineries
present in the JSON; refineries not mentioned in the JSON are left alone.

JSON shape:

    {
      "version": "1",
      "generated_at": "2026-05-20",
      "refineries": [
        {
          "refinery_id": "ref_exxon_baytown",
          "name": "ExxonMobil Baytown",
          "grades": [
            {"commodity": "wti",
             "is_primary": true,
             "source": "company_disclosure",
             "notes": "Disclosed as primary feedstock in ExxonMobil 10-K 2024."},
            ...
          ]
        },
        ...
      ]
    }

Validation:
  - Every `refinery_id` must exist in `oil_network.assets` as a physical
    `ref_*` row. Unknown refinery_ids abort with a list.
  - Every `commodity` must exist in `oil_network.commodities`. Unknown
    commodities abort with a list.
  - No two rows for the same (refinery_id, commodity) within one refinery
    entry — duplicates within a JSON refinery abort.

Idempotent. Run via:

    ..\\..\\.venv\\Scripts\\python.exe code\\load_refinery_grades.py
"""
from __future__ import annotations

import json
import sys

import psycopg2
from psycopg2.extras import execute_values

from paths import CONFIG_DIR

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SEED_PATH = CONFIG_DIR / "refinery_grades.json"


def _validate(payload: dict, known_refineries: set[str], known_grades: set[str]) -> list[tuple]:
    """Return the flat row list, or sys.exit with a clear error."""
    rows: list[tuple] = []
    unknown_refineries: list[str] = []
    unknown_grades: list[tuple[str, str]] = []
    dup_pairs: list[tuple[str, str]] = []

    for entry in payload.get("refineries", []):
        rid = entry["refinery_id"]
        if rid not in known_refineries:
            unknown_refineries.append(rid)
            continue
        seen_local: set[str] = set()
        for g in entry.get("grades", []):
            comm = g["commodity"]
            if comm not in known_grades:
                unknown_grades.append((rid, comm))
                continue
            if comm in seen_local:
                dup_pairs.append((rid, comm))
                continue
            seen_local.add(comm)
            rows.append((
                rid,
                comm,
                bool(g.get("is_primary", False)),
                g.get("source"),
                g.get("notes"),
            ))

    fatal = False
    if unknown_refineries:
        print(f"\nERROR — {len(unknown_refineries)} unknown refinery_id(s) in JSON:")
        for r in unknown_refineries[:10]:
            print(f"    {r}")
        if len(unknown_refineries) > 10:
            print(f"    ... and {len(unknown_refineries) - 10} more")
        fatal = True
    if unknown_grades:
        print(f"\nERROR — {len(unknown_grades)} unknown commodity reference(s):")
        for rid, comm in unknown_grades[:10]:
            print(f"    {rid} -> {comm}")
        if len(unknown_grades) > 10:
            print(f"    ... and {len(unknown_grades) - 10} more")
        fatal = True
    if dup_pairs:
        print(f"\nERROR — {len(dup_pairs)} duplicate (refinery, commodity) within JSON:")
        for rid, comm in dup_pairs[:10]:
            print(f"    {rid} / {comm}")
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
        cur.execute(r"""
            SELECT asset_id FROM oil_network.assets
            WHERE asset_id LIKE 'ref\_%' ESCAPE '\' AND kind = 'physical'
        """)
        known_refineries = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT commodity FROM oil_network.commodities")
        known_grades = {r[0] for r in cur.fetchall()}

        print(f"[1] validating JSON ({len(payload.get('refineries', []))} refinery entries)")
        rows = _validate(payload, known_refineries, known_grades)
        print(f"    -> {len(rows)} (refinery, grade) assignments validated")

        # Replace assignments only for refineries the JSON actually mentions.
        # Leaves rows for other refineries untouched.
        mentioned = sorted({r[0] for r in rows})
        if mentioned:
            print(f"[2] deleting existing assignments for {len(mentioned)} mentioned refineries")
            cur.execute(
                "DELETE FROM refineries.refinery_grade_assignments "
                "WHERE refinery_id = ANY(%s)",
                (mentioned,),
            )

        if rows:
            print(f"[3] inserting {len(rows)} fresh rows")
            execute_values(
                cur,
                "INSERT INTO refineries.refinery_grade_assignments "
                "(refinery_id, commodity, is_primary, source, notes) VALUES %s",
                rows, page_size=500,
            )

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM refineries.refinery_grade_assignments")
        n_total = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(DISTINCT refinery_id),
                   COUNT(DISTINCT commodity)
            FROM refineries.refinery_grade_assignments
        """)
        n_refineries, n_grades = cur.fetchone()
        print(f"\nFinal state:")
        print(f"  refinery_grade_assignments rows: {n_total}")
        print(f"  distinct refineries:             {n_refineries}")
        print(f"  distinct grades:                 {n_grades}")

        cur.execute("""
            SELECT COUNT(*) FROM refineries.v_refinery_grade_audit
            WHERE mismatch_flag IS NOT NULL
        """)
        n_flagged = cur.fetchone()[0]
        print(f"  audit mismatches:                {n_flagged}   (see "
              f"refineries.v_refinery_grade_audit for details)")


if __name__ == "__main__":
    main()
