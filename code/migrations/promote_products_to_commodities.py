"""Promote products.oil_products into oil_network.commodities + hierarchy.

Lifts the staged IEA / JODI oil-products tree (65 rows in
`products.oil_products`, loaded by `build_products_schema.py`) into the
working commodity registry that the resolver and variable layer already
build on:

  * Inserts one row into `oil_network.commodities` per product, EXCEPT for
    `crude_oil` which is mapped onto the existing `crude` root (they
    denote the same concept, and `crude` already has all the US grade
    subtree, variable bindings and FK references hanging off it).
  * Inserts the corresponding parent->child edges into
    `oil_network.commodity_hierarchy`. The `crude_oil` slug is rewritten
    to `crude` on both sides of every edge it appears in, so the existing
    crude subtree gains `primary_oil` (and `oil`) as ancestors without
    breaking any existing FK.

Result after this migration runs:

    oil
    |-- primary_oil
    |    |-- crude  (was 'crude_oil' in products; existing US grade
    |    |          subtree (wti / bakken_light / ... / kern_heavy) stays
    |    |          intact underneath)
    |    |-- ngl
    |    |-- refinery_feedstocks
    |    |-- additives_oxygenates
    |    `-- other_hydrocarbons
    `-- secondary_oil
         |-- refinery_gas, ethane, naphtha, gas_diesel_oil(+6), fuel_oil(+5),
         |-- lpg(+4: propane, n_butane, isobutane, lpg_mix),
         |-- gasoline -> aviation_gasoline, motor_gasoline(+5: regular, mid,
         |                premium, RBOB, CBOB),
         |-- jet_kerosene -> gas-type, kero-type(+5: Jet A, Jet A-1, JP-5,
         |                    JP-8, TS-1), other_kerosene,
         `-- other_products -> white_spirit_sbp, lubricants(+4), bitumen(+5),
                                paraffin_waxes, petroleum_coke(+3),
                                other_oil_products_misc

Storage strategy:

  * `oil_network.commodities` stays the slim canonical registry it
    already is. Only `commodity`, `description` and `attributes` are
    written; the crude-grade-specific columns (`sweet_sour`,
    `density_class`, `api_gravity_*`, `sulfur_pct_*`, `region`,
    `typical_basin`) remain NULL for refined products and aggregates.
  * The richer IEA / JODI metadata (iea_code, jodi_code, state_of_matter,
    density bands, boiling ranges, sort_order, full description, source,
    plus any free-form attributes from the staging table) folds into
    `commodities.attributes` under an `ieajodi` key — so nothing is lost
    but the canonical table stays narrow.
  * The master copy of product metadata stays in `products.oil_products`
    and is unchanged by this script.

Idempotent — re-running is a no-op:

  * Commodities: `ON CONFLICT (commodity) DO UPDATE` overwrites
    description + merges the `ieajodi` JSONB block.
  * Hierarchy: `ON CONFLICT DO NOTHING` for parent-child edges.

Run via:

    ..\\..\\.venv\\Scripts\\python.exe code\\migrations\\promote_products_to_commodities.py
"""
from __future__ import annotations

import json
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# 'crude_oil' (in products schema) and 'crude' (in oil_network) denote the
# same concept. Map the former onto the latter on both sides of every
# hierarchy edge and skip inserting a duplicate commodity row.
PRODUCT_TO_COMMODITY = {"crude_oil": "crude"}


SELECT_PRODUCTS_SQL = """
SELECT
    product_code,
    parent_code,
    name,
    description,
    category,
    iea_code,
    jodi_code,
    state_of_matter,
    typical_density_kg_m3_min,
    typical_density_kg_m3_max,
    boiling_range_c_min,
    boiling_range_c_max,
    sort_order,
    source,
    attributes
FROM products.oil_products
ORDER BY sort_order NULLS LAST, product_code
"""


UPSERT_COMMODITY_SQL = """
INSERT INTO oil_network.commodities
    (commodity, description, attributes)
VALUES %s
ON CONFLICT (commodity) DO UPDATE SET
    description = EXCLUDED.description,
    attributes  = oil_network.commodities.attributes || EXCLUDED.attributes
"""


# Special-case merge for the 'crude' row: keep the existing description
# (preserves 'Crude oil (generic root)' shown elsewhere) but fold the IEA
# metadata into attributes alongside whatever is already there.
MERGE_CRUDE_ATTRS_SQL = """
UPDATE oil_network.commodities
   SET attributes = attributes || %s::jsonb
 WHERE commodity = 'crude'
"""


def to_jsonable(value):
    """psycopg2 returns NUMERIC as Decimal; JSON can't serialise that."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def build_ieajodi_block(
    name, desc, category, iea_code, jodi_code, phase,
    d_min, d_max, b_min, b_max, sort_order, source, attrs,
):
    """Compose the JSONB payload that lives under attributes.ieajodi."""
    block = {
        "name": name,
        "description": desc,
        "category": category,
        "iea_code": iea_code,
        "jodi_code": jodi_code,
        "state_of_matter": phase,
        "typical_density_kg_m3_min": to_jsonable(d_min),
        "typical_density_kg_m3_max": to_jsonable(d_max),
        "boiling_range_c_min":       to_jsonable(b_min),
        "boiling_range_c_max":       to_jsonable(b_max),
        "sort_order": sort_order,
        "source": source,
    }
    # Drop None to keep JSON compact.
    block = {k: v for k, v in block.items() if v is not None}
    # Preserve any free-form attributes from the staging row.
    if attrs:
        block["extra"] = attrs
    return block


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(SELECT_PRODUCTS_SQL)
        product_rows = cur.fetchall()
        print(f"[0] loaded {len(product_rows)} rows from products.oil_products")

        commodity_rows = []
        hierarchy_edges: list[tuple[str, str]] = []
        crude_merge_payload = None  # set when we hit the crude_oil row

        for (code, parent, name, desc, category, iea_code, jodi_code, phase,
             d_min, d_max, b_min, b_max, sort_order, source, attrs) in product_rows:

            # Apply the crude_oil -> crude remap on both sides of the edge.
            mapped_code   = PRODUCT_TO_COMMODITY.get(code, code)
            mapped_parent = PRODUCT_TO_COMMODITY.get(parent, parent) if parent else None

            ieajodi_block = build_ieajodi_block(
                name, desc, category, iea_code, jodi_code, phase,
                d_min, d_max, b_min, b_max, sort_order, source, attrs,
            )
            commodity_attrs = {"ieajodi": ieajodi_block}

            if code == "crude_oil":
                # Don't overwrite the existing 'crude' row — just merge the
                # IEA metadata in. The description stays whatever was there.
                crude_merge_payload = json.dumps(commodity_attrs)
            else:
                commodity_rows.append((mapped_code, desc, json.dumps(commodity_attrs)))

            if mapped_parent is not None:
                hierarchy_edges.append((mapped_parent, mapped_code))

        # Snapshot before-state for the diff at the end.
        cur.execute("SELECT COUNT(*) FROM oil_network.commodities")
        n_commod_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM oil_network.commodity_hierarchy")
        n_edges_before = cur.fetchone()[0]

        print(f"[1] upsert {len(commodity_rows)} commodity rows "
              f"(crude_oil merged into existing 'crude')")
        execute_values(cur, UPSERT_COMMODITY_SQL, commodity_rows, page_size=200)

        if crude_merge_payload is not None:
            print("[2] merge IEA metadata into existing 'crude' row")
            cur.execute(MERGE_CRUDE_ATTRS_SQL, (crude_merge_payload,))

        print(f"[3] upsert {len(hierarchy_edges)} parent->child hierarchy edges")
        execute_values(
            cur,
            "INSERT INTO oil_network.commodity_hierarchy "
            "(parent_commodity, child_commodity) VALUES %s "
            "ON CONFLICT DO NOTHING",
            hierarchy_edges, page_size=200,
        )

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM oil_network.commodities")
        n_commod_after = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM oil_network.commodity_hierarchy")
        n_edges_after = cur.fetchone()[0]

        print()
        print(f"commodities:        {n_commod_before:>3}  -> {n_commod_after:>3} "
              f"(+{n_commod_after - n_commod_before})")
        print(f"hierarchy edges:    {n_edges_before:>3}  -> {n_edges_after:>3} "
              f"(+{n_edges_after - n_edges_before})")

        print("\nUnified tree (depth-first descent of oil_network.commodity_hierarchy):")
        cur.execute("""
            WITH RECURSIVE t AS (
                SELECT NULL::TEXT AS parent_commodity,
                       commodity AS child_commodity,
                       1         AS depth,
                       ARRAY[commodity]::TEXT[] AS path
                FROM oil_network.commodities
                WHERE commodity NOT IN (
                    SELECT child_commodity FROM oil_network.commodity_hierarchy
                )
                UNION ALL
                SELECT h.parent_commodity, h.child_commodity, t.depth + 1,
                       t.path || h.child_commodity
                FROM oil_network.commodity_hierarchy h
                JOIN t ON h.parent_commodity = t.child_commodity
            )
            SELECT depth, child_commodity, parent_commodity
            FROM t
            ORDER BY path
        """)
        for depth, commodity, parent in cur.fetchall():
            indent = "  " * (depth - 1)
            arrow  = "  (root)" if parent is None else f"  <- {parent}"
            print(f"  {indent}{commodity}{arrow}")


if __name__ == "__main__":
    main()
