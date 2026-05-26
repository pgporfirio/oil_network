"""Build the initial map of US crude grades.

Extends oil_network.commodities with classification columns (sweet/sour,
density class, API gravity range, sulfur range, region, typical basin) and
creates oil_network.commodity_hierarchy for parent -> child relationships
between grades.

Populates the major US grades (WTI and its delivery points, Bakken Light,
Eagle Ford light + condensate, LLS, Niobrara, Oklahoma sweet, the Gulf
medium-sour family, Alaska North Slope, California heavy) plus a simple
tree:
  crude (root)
   |-- wti
   |    |-- wti_midland
   |    |-- wti_cushing
   |    |-- wti_houston
   |-- bakken_light, eagle_ford_light, eagle_ford_condensate, lls,
   |   niobrara_sweet, oklahoma_sweet  (light sweet)
   |-- mars, poseidon, thunder_horse, southern_green_canyon, ans
   |   (medium sour)
   |-- california_heavy
        |-- kern_heavy
        |-- midway_sunset

Classification ranges are typical published values (RBN Energy / Argus /
Platts crude assessments). The framework does not yet propagate grade
through variables — this is the registry that downstream per-grade
decomposition (Section 4.9 of the thesis) will bind into.

Idempotent — re-running is a no-op for both DDL and data.
"""
from __future__ import annotations

import psycopg2
from psycopg2.extras import execute_values

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


SCHEMA_DDL = """
ALTER TABLE oil_network.commodities
    ADD COLUMN IF NOT EXISTS sweet_sour       TEXT,
    ADD COLUMN IF NOT EXISTS density_class    TEXT,
    ADD COLUMN IF NOT EXISTS api_gravity_min  NUMERIC,
    ADD COLUMN IF NOT EXISTS api_gravity_max  NUMERIC,
    ADD COLUMN IF NOT EXISTS sulfur_pct_min   NUMERIC,
    ADD COLUMN IF NOT EXISTS sulfur_pct_max   NUMERIC,
    ADD COLUMN IF NOT EXISTS region           TEXT,
    ADD COLUMN IF NOT EXISTS typical_basin    TEXT;

CREATE TABLE IF NOT EXISTS oil_network.commodity_hierarchy (
    parent_commodity TEXT NOT NULL REFERENCES oil_network.commodities(commodity) ON DELETE CASCADE,
    child_commodity  TEXT NOT NULL REFERENCES oil_network.commodities(commodity) ON DELETE CASCADE,
    PRIMARY KEY (parent_commodity, child_commodity),
    CHECK (parent_commodity <> child_commodity)
);

CREATE INDEX IF NOT EXISTS idx_commodity_hierarchy_parent
    ON oil_network.commodity_hierarchy (parent_commodity);
CREATE INDEX IF NOT EXISTS idx_commodity_hierarchy_child
    ON oil_network.commodity_hierarchy (child_commodity);

-- Convenience view: full ancestor chain for a child commodity via recursion.
CREATE OR REPLACE VIEW oil_network.v_commodity_ancestors AS
WITH RECURSIVE chain AS (
    SELECT child_commodity AS commodity,
           parent_commodity,
           1 AS depth
    FROM oil_network.commodity_hierarchy
    UNION ALL
    SELECT c.commodity,
           h.parent_commodity,
           c.depth + 1
    FROM chain c
    JOIN oil_network.commodity_hierarchy h
      ON h.child_commodity = c.parent_commodity
)
SELECT commodity, parent_commodity AS ancestor_commodity, depth FROM chain;
"""


# (commodity, description, sweet_sour, density_class,
#  api_min, api_max, sulfur_min, sulfur_max, region, typical_basin)
COMMODITIES = [
    # Root
    ("crude", "Crude oil (generic root)",
     None, None, None, None, None, None, None, None),

    # ---- Light sweet US grades ----
    ("wti", "West Texas Intermediate — generic WTI grade as quoted on NYMEX",
     "sweet", "light", 38, 42, 0.2, 0.5, "US", "Permian"),
    ("wti_midland", "WTI Midland — Permian wellhead delivery",
     "sweet", "light", 40, 44, 0.2, 0.4, "US", "Permian"),
    ("wti_cushing", "WTI Cushing — pipeline delivery at the storage hub",
     "sweet", "light", 38, 42, 0.3, 0.5, "US", "Permian"),
    ("wti_houston", "WTI Houston — Gulf-Coast export delivery (MEH)",
     "sweet", "light", 38, 42, 0.3, 0.5, "US", "Permian"),
    ("bakken_light", "Bakken Light Sweet",
     "sweet", "light", 40, 44, 0.1, 0.3, "US", "Bakken"),
    ("eagle_ford_light", "Eagle Ford Light Sweet",
     "sweet", "light", 38, 50, 0.1, 0.3, "US", "Eagle Ford"),
    ("eagle_ford_condensate", "Eagle Ford Condensate (very light)",
     "sweet", "very_light", 50, 70, 0.05, 0.2, "US", "Eagle Ford"),
    ("lls", "Louisiana Light Sweet (LLS)",
     "sweet", "light", 35, 40, 0.3, 0.5, "US", "Gulf Coast"),
    ("niobrara_sweet", "Niobrara Sweet — DJ basin Wyoming / Colorado",
     "sweet", "light", 35, 42, 0.2, 0.4, "US", "Niobrara"),
    ("oklahoma_sweet", "Oklahoma sweet — Anadarko / SCOOP / STACK",
     "sweet", "light", 36, 42, 0.2, 0.5, "US", "Oklahoma"),

    # ---- Medium sour US grades (Gulf + Alaska) ----
    ("mars", "Mars Blend (offshore Gulf of America)",
     "medium_sour", "medium", 28, 32, 1.5, 2.0, "US", "Gulf of America"),
    ("poseidon", "Poseidon (offshore Gulf of America)",
     "medium_sour", "medium", 28, 32, 1.5, 2.0, "US", "Gulf of America"),
    ("thunder_horse", "Thunder Horse (offshore Gulf of America)",
     "medium_sour", "medium", 32, 35, 0.8, 1.0, "US", "Gulf of America"),
    ("southern_green_canyon", "Southern Green Canyon (offshore Gulf)",
     "medium_sour", "medium", 28, 32, 1.6, 2.2, "US", "Gulf of America"),
    ("ans", "Alaska North Slope (ANS)",
     "medium_sour", "medium", 29, 33, 0.8, 1.0, "US", "Alaska North Slope"),

    # ---- Heavy sour California grades ----
    ("california_heavy", "California heavy crude — San Joaquin basin family",
     "sour", "heavy", 13, 25, 0.8, 1.5, "US", "California"),
    ("kern_heavy", "Kern County heavy (CA)",
     "sour", "heavy", 12, 16, 1.0, 1.5, "US", "California"),
    ("midway_sunset", "Midway-Sunset heavy (CA)",
     "sour", "heavy", 12, 14, 1.2, 1.6, "US", "California"),
]


# (parent_commodity, child_commodity)
HIERARCHY = [
    # ---- Direct children of the root ----
    ("crude", "wti"),
    ("crude", "bakken_light"),
    ("crude", "eagle_ford_light"),
    ("crude", "eagle_ford_condensate"),
    ("crude", "lls"),
    ("crude", "niobrara_sweet"),
    ("crude", "oklahoma_sweet"),
    ("crude", "mars"),
    ("crude", "poseidon"),
    ("crude", "thunder_horse"),
    ("crude", "southern_green_canyon"),
    ("crude", "ans"),
    ("crude", "california_heavy"),

    # ---- Second-level children ----
    ("wti", "wti_midland"),
    ("wti", "wti_cushing"),
    ("wti", "wti_houston"),
    ("california_heavy", "kern_heavy"),
    ("california_heavy", "midway_sunset"),
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
        print("[1] schema DDL (ALTER + CREATE TABLE + CREATE VIEW)")
        cur.execute(SCHEMA_DDL)

        print(f"[2] upsert {len(COMMODITIES)} commodities")
        rows = [c + ('{}',) for c in COMMODITIES]
        # Need a primary-key constraint on (commodity) for ON CONFLICT — make sure it exists
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'oil_network.commodities'::regclass
                      AND contype = 'p'
                ) THEN
                    ALTER TABLE oil_network.commodities
                        ADD CONSTRAINT commodities_pkey PRIMARY KEY (commodity);
                END IF;
            END$$;
        """)
        execute_values(cur, UPSERT_COMMODITY_SQL, rows, page_size=200)

        print(f"[3] upsert {len(HIERARCHY)} parent->child edges")
        execute_values(
            cur,
            "INSERT INTO oil_network.commodity_hierarchy (parent_commodity, child_commodity) "
            "VALUES %s ON CONFLICT DO NOTHING",
            HIERARCHY, page_size=200,
        )

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM oil_network.commodities")
        n_commod = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM oil_network.commodity_hierarchy")
        n_edges = cur.fetchone()[0]
        print(f"\nFinal state: {n_commod} commodities, {n_edges} hierarchy edges")
        print("\nSample tree (depth-first from 'crude'):")
        cur.execute("""
            WITH RECURSIVE t AS (
                SELECT parent_commodity, child_commodity, 1 AS depth
                FROM oil_network.commodity_hierarchy
                WHERE parent_commodity = 'crude'
                UNION ALL
                SELECT h.parent_commodity, h.child_commodity, t.depth + 1
                FROM oil_network.commodity_hierarchy h
                JOIN t ON h.parent_commodity = t.child_commodity
            )
            SELECT depth, parent_commodity, child_commodity FROM t
            ORDER BY depth, child_commodity
        """)
        for d, p, c in cur.fetchall():
            indent = "  " * d
            print(f"  {indent}{c}  (parent: {p})")


if __name__ == "__main__":
    main()
