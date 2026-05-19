"""Populate `formula_inputs` on TS-bound aggregate variables (USA, PADDs, districts).

This makes the aggregation hierarchy explicit and queryable, even though the
aggregate's VALUE is the TS observation. Semantics introduced by this pass:

  * timeseries_id set        -> variable's value comes from the TS (authoritative)
  * formula_inputs populated -> declares the constituents that should aggregate
                                to this variable's value
  * Both can coexist on the same row.

When both are present, the future formula evaluator (and the v_aggregation_consistency
view) computes sum(constituents) and compares to the TS value. Material gap = data
inconsistency flag.

Constituents added in this pass (the clean cases where every constituent is
observed/derived under the current scope):

  - usa_view.{production,consumption,inventory}                <- paddX_view × 5
  - usa_view.inflow__from__{foreign_supply,canadian_oil_sands} <- paddX_view × 5
  - usa_view.outflow__to__foreign_export_destination           <- paddX_view × 5

  - padd[1-3]_view.consumption                <- district_R*_refining_view × N
    PADDs 4/5 have only one refining district, which IS paddX_view (no sub-district)
    so no constituents added for those.

  - paddX_view.production                     <- production-side physical assets in PADD X
    (basin-state nodes + paddX_other residual)

  - paddX_view.inventory                      <- storage hubs in PADD X
    (PADD 3 additionally includes SPR sites in PADD 3)

  - district_R*_refining_view.consumption     <- refineries in that district
    (using each refinery's configuration.duoarea_code)

  - permian.production    <- permian_tx + permian_nm
  - bakken.production     <- bakken_nd + bakken_mt
  - eagle_ford.production <- eagle_ford_tx

Idempotent — re-runs UPSERT the same lists.
"""
from __future__ import annotations

import sys
from collections import defaultdict
import psycopg2
import psycopg2.extras

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, "reconfigure") else None

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"
EFFECTIVE_FROM = "2015-01-01"


def fetch_assets():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.node_subtype, l.padd, l.state,
                   a.attributes->'configuration'->>'duoarea_code' AS district_code
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
        """)
        return [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]


def derive_district(asset: dict) -> str | None:
    code = asset.get("district_code")
    if code:
        return code
    # 3 named refineries without district_code — fall back by PADD
    padd = asset.get("padd") or ""
    if "4" in padd:
        return "R40"
    if "5" in padd:
        return "R50"
    return None


def padd_digit(asset: dict) -> str | None:
    p = asset.get("padd") or ""
    for ch in p:
        if ch in "12345":
            return ch
    return None


def build_constituents(assets):
    """Return {parent_variable_id: [child_variable_id, ...]} mapping."""
    out = defaultdict(list)
    by_id = {a["asset_id"]: a for a in assets}

    # ---------- USA = sum of 5 PADDs (P, C, S + boundary flows) ----------
    for vt in ("production", "consumption", "inventory"):
        out[f"{vt}__crude__usa_view"] = [
            f"{vt}__crude__padd{x}_view" for x in "12345"
        ]
    out["inflow__crude__usa_view__foreign_supply"] = [
        f"inflow__crude__padd{x}_view__foreign_supply" for x in "12345"
    ]
    out["inflow__crude__usa_view__canadian_oil_sands"] = [
        f"inflow__crude__padd{x}_view__canadian_oil_sands" for x in "12345"
    ]
    out["outflow__crude__usa_view__foreign_export_destination"] = [
        f"outflow__crude__padd{x}_view__foreign_export_destination" for x in "12345"
    ]
    # USA balancing_item also aggregates (its closure formula already references
    # same-node inputs, but the cross-PADD aggregation identity is useful documentation)
    out["balancing_item__crude__usa_view"] = [
        f"balancing_item__crude__padd{x}_view" for x in "12345"
    ]

    # ---------- PADD consumption = districts (PADDs 1-3) ----------
    refineries = [a for a in assets if a["node_subtype"] == "refinery"]
    refineries_by_padd = defaultdict(list)
    refineries_by_district = defaultdict(list)
    for r in refineries:
        pd = padd_digit(r)
        if pd:
            refineries_by_padd[pd].append(r["asset_id"])
        d = derive_district(r)
        if d:
            refineries_by_district[d].append(r["asset_id"])

    # PADD 1 = REC + RAP; PADD 2 = R2A,R2B,R2C; PADD 3 = R3A..R3E.
    padd_to_districts = {
        "1": ["REC", "RAP"],
        "2": ["R2A", "R2B", "R2C"],
        "3": ["R3A", "R3B", "R3C", "R3D", "R3E"],
    }
    for pd, dcodes in padd_to_districts.items():
        out[f"consumption__crude__padd{pd}_view"] = [
            f"consumption__crude__district_{d}_refining_view" for d in dcodes
        ]

    # PADDs 4 and 5: single district -- refineries roll up directly to paddX_view.
    for pd in ("4", "5"):
        out[f"consumption__crude__padd{pd}_view"] = [
            f"consumption__crude__{r}" for r in sorted(refineries_by_padd.get(pd, []))
        ]

    # District consumption = refineries in district (PADDs 1-3)
    for d_code in ["REC", "RAP", "R2A", "R2B", "R2C", "R3A", "R3B", "R3C", "R3D", "R3E"]:
        refs = refineries_by_district.get(d_code, [])
        if refs:
            out[f"consumption__crude__district_{d_code}_refining_view"] = [
                f"consumption__crude__{r}" for r in sorted(refs)
            ]

    # ---------- PADD production = state/basin nodes in PADD + PADD residual ----------
    # The production-side rollups (per HANDOVER decision 30): each PADD has its
    # modelled basin/state production nodes plus a padd*_other residual.
    padd_production = {
        "1": ["padd1_other"],
        "2": ["bakken_nd", "oklahoma_conventional", "padd2_other"],
        "3": ["permian_tx", "permian_nm", "eagle_ford_tx", "gulf_of_america",
              "texas_other", "padd3_other"],
        "4": ["bakken_mt", "wyoming_conventional", "colorado_conventional",
              "montana_other", "padd4_other"],
        "5": ["alaska_north_slope", "california_conventional", "padd5_other"],
    }
    for pd, children in padd_production.items():
        out[f"production__crude__padd{pd}_view"] = [
            f"production__crude__{c}" for c in children
        ]

    # ---------- PADD inventory = hubs + SPR (PADD 3 only) ----------
    hubs_by_padd = defaultdict(list)
    for a in assets:
        if a["node_subtype"] in ("storage_terminal", "spr_site"):
            pd = padd_digit(a)
            if pd:
                hubs_by_padd[pd].append(a["asset_id"])

    for pd, hubs in hubs_by_padd.items():
        if hubs:
            out[f"inventory__crude__padd{pd}_view"] = [
                f"inventory__crude__{h}" for h in sorted(hubs)
            ]

    # ---------- Basin production = basin-state nodes ----------
    out["production__crude__permian"]    = ["production__crude__permian_tx", "production__crude__permian_nm"]
    out["production__crude__bakken"]     = ["production__crude__bakken_nd", "production__crude__bakken_mt"]
    out["production__crude__eagle_ford"] = ["production__crude__eagle_ford_tx"]

    return dict(out)


def main():
    print("Querying asset graph...")
    assets = fetch_assets()
    print(f"  loaded {len(assets)} assets")

    constituents = build_constituents(assets)
    print(f"  computed {len(constituents)} aggregate variables with explicit constituents")

    # Filter to only those parent variables that actually exist in variable_assignments,
    # and only keep children that exist as variables.
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT variable_id FROM oil_network.variables")
        existing_vars = set(r[0] for r in cur.fetchall())
        cur.execute("""SELECT variable_id FROM oil_network.variable_assignments
                       WHERE scenario_id=%s AND effective_from=%s""",
                    (SCENARIO, EFFECTIVE_FROM))
        existing_assignments = set(r[0] for r in cur.fetchall())

    updates = []
    skipped = []
    for parent_var, children in constituents.items():
        if parent_var not in existing_assignments:
            skipped.append(("parent_missing", parent_var))
            continue
        valid_children = [c for c in children if c in existing_vars]
        if not valid_children:
            skipped.append(("no_valid_children", parent_var))
            continue
        updates.append((parent_var, valid_children))

    print(f"  to update: {len(updates)} variables; skipped: {len(skipped)}")

    with psycopg2.connect(**DB) as conn:
        with conn.cursor() as cur:
            for parent_var, children in updates:
                cur.execute("""
                    UPDATE oil_network.variable_assignments
                    SET formula_inputs = %s
                    WHERE scenario_id = %s
                      AND variable_id = %s
                      AND effective_from = %s
                """, (children, SCENARIO, parent_var, EFFECTIVE_FROM))
        conn.commit()
    print(f"  updated {len(updates)} variable_assignments rows with formula_inputs")

    # Sample output
    print("\nSample (first 5 updates):")
    for parent_var, children in updates[:5]:
        print(f"  {parent_var}")
        for c in children[:3]:
            print(f"     -> {c}")
        if len(children) > 3:
            print(f"     -> ... ({len(children)-3} more)")


if __name__ == "__main__":
    main()
