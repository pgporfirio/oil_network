"""Stage-2 grade expansion: add Wyoming + Permian-light + Permian condensate.

Adds four commodities not in the stage-1 grade map, with their typical API /
sulfur / region / basin metadata, and wires them as direct children of the
`crude` root:

  - wyoming_sweet         — conventional sweet Wyoming (Frontier/Niobrara WY)
  - wyoming_asphaltic     — heavy sour Wyoming (Salt Creek-type production)
  - wtl                   — West Texas Light (light Permian/Delaware shale grade,
                            distinct from WTI Midland; NYMEX/CME WTL contract)
  - permian_condensate    — very-light Permian lease condensate

Motivation: the stage-1 grade map had no Wyoming-specific grade (WY was
borrowing `niobrara_sweet`, which conflates DJ basin Colorado with WY), and
Permian production was collapsed onto `wti_midland` alone, hiding the lighter
WTL and condensate streams that have grown materially since the shale boom.

Idempotent (ON CONFLICT) — re-running is a no-op.

Reference values: RBN Energy crude assessments, Argus US Crude, Platts Crude
Oil Marketwire. Ranges are typical published bands.
"""
from __future__ import annotations

import psycopg2
from psycopg2.extras import execute_values

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# (commodity, description, sweet_sour, density_class,
#  api_min, api_max, sulfur_min, sulfur_max, region, typical_basin)
NEW_COMMODITIES = [
    ("wyoming_sweet",
     "Sweet Wyoming — Frontier / Niobrara WY production",
     "sweet", "light", 35, 42, 0.2, 0.5, "US", "Wyoming"),

    ("wyoming_asphaltic",
     "Wyoming Asphaltic Sour — Salt Creek-type heavy sour WY production",
     "sour", "heavy", 18, 26, 1.8, 3.0, "US", "Wyoming"),

    ("wtl",
     "West Texas Light (WTL) — light Permian / Delaware shale grade, distinct from WTI Midland; CME WTL contract",
     "sweet", "very_light", 44, 50, 0.05, 0.25, "US", "Permian"),

    ("permian_condensate",
     "Permian lease condensate — very-light Permian wellhead stream",
     "sweet", "very_light", 50, 70, 0.05, 0.15, "US", "Permian"),
]


NEW_HIERARCHY = [
    ("crude", "wyoming_sweet"),
    ("crude", "wyoming_asphaltic"),
    ("crude", "wtl"),
    ("crude", "permian_condensate"),
]


UPSERT_COMMODITY_SQL = """
INSERT INTO oil_network.commodities
    (commodity, description, sweet_sour, density_class,
     api_gravity_min, api_gravity_max, sulfur_pct_min, sulfur_pct_max,
     region, typical_basin, attributes)
VALUES
    %s
ON CONFLICT (commodity) DO UPDATE SET
    description     = EXCLUDED.description,
    sweet_sour      = EXCLUDED.sweet_sour,
    density_class   = EXCLUDED.density_class,
    api_gravity_min = EXCLUDED.api_gravity_min,
    api_gravity_max = EXCLUDED.api_gravity_max,
    sulfur_pct_min  = EXCLUDED.sulfur_pct_min,
    sulfur_pct_max  = EXCLUDED.sulfur_pct_max,
    region          = EXCLUDED.region,
    typical_basin   = EXCLUDED.typical_basin;
"""


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print(f"[1] upsert {len(NEW_COMMODITIES)} new commodities")
        rows = [c + ('{}',) for c in NEW_COMMODITIES]
        execute_values(cur, UPSERT_COMMODITY_SQL, rows, page_size=50)

        print(f"[2] upsert {len(NEW_HIERARCHY)} new parent->child edges")
        execute_values(
            cur,
            "INSERT INTO oil_network.commodity_hierarchy (parent_commodity, child_commodity) "
            "VALUES %s ON CONFLICT DO NOTHING",
            NEW_HIERARCHY, page_size=50,
        )

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM oil_network.commodities")
        n_commod = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM oil_network.commodity_hierarchy")
        n_edges = cur.fetchone()[0]
        print(f"\nFinal state: {n_commod} commodities, {n_edges} hierarchy edges")

        print("\nNewly added commodities (verify):")
        cur.execute("""
            SELECT commodity, description, api_gravity_min, api_gravity_max,
                   sulfur_pct_min, sulfur_pct_max, region, typical_basin
            FROM oil_network.commodities
            WHERE commodity = ANY(%s)
            ORDER BY commodity
        """, ([c[0] for c in NEW_COMMODITIES],))
        for r in cur.fetchall():
            print(f"  {r[0]:22s} {r[1][:70]}")
            print(f"  {'':22s}   API {r[2]}-{r[3]}, S% {r[4]}-{r[5]}, "
                  f"{r[6]} / {r[7]}")


if __name__ == "__main__":
    main()
