"""Split `bakken_nd_gathering` into ND + MT gathering nodes with a cross-state connector.

The starter graph carries a single `bakken_nd_gathering` node receiving from both
`bakken_nd` (PADD 2) and `bakken_mt` (PADD 4), with outflows fanning across both
PADDs. That made the gathering belong cleanly to neither partition and forced
producing basins' outflows-to-gathering to appear as boundary outflows in
`audit_partition_gaps.py` for both padd2_view and padd4_view (the gap.O artefact
flagged in the tenth-pass handover).

This migration splits the gathering along state lines while preserving the
physical reality of pooled cross-state midstream networks (Hess, Crestwood,
Energy Transfer operate ND/MT lines as a single commercial system):

  bakken_nd ──→ bakken_nd_gathering ──→ {PADD-2 destinations: clearbrook, johnsons_corner,
                    │     ▲              tesoro_mandan, flint_st_paul, st_paul,
                    ▼     │              superior_superior, hf_evansville}
              pipe_bakken_xstate
              (bidirectional, latent — Principle 2.11)
                    ▲     │
                    │     ▼
  bakken_mt ──→ bakken_mt_gathering ──→ {PADD-4 destinations: calumet_great_falls,
                                          cenex_laurel, par_billings, phillips_billings,
                                          silver_evanston, wyoming_newcastle}

The connector `pipe_bakken_xstate` is a multi-PADD pipeline-type aggregate carrying
the latent cross-state flow. No public TS measures inter-state Bakken gathering
transfers, so all four connector variables stay latent in the starter scenario.
Future commercial gathering telemetry (or per-grade decomposition) would land here.

Also adds inventory-child declarations on each padd_view for its gathering nodes
(numerical effect: zero, since gathering S=0 by default; audit effect: gathering
enters descendants(padd_N), so basin→gathering outflows count as intra not bdy,
closing the gap.O artefact). Same treatment for all 6 gathering nodes:
  padd2_view inventory += [bakken_nd_gathering]
  padd3_view inventory += [eagle_ford_gathering, permian_nm_gathering, permian_tx_gathering]
  padd4_view inventory += [bakken_mt_gathering]
  padd5_view inventory += [ans_gathering]

Idempotent: re-running upserts to the same state.

After running this script, re-run:
  python resolve_scenario.py
  python audit_partition_gaps.py
  python audit_ts_binding_uniqueness.py
"""

from __future__ import annotations

import sys
import psycopg2
from psycopg2.extras import Json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"
GRAPH_SQL = "(SELECT graph_id FROM oil_network.graphs LIMIT 1)"

PADD4_REFINERIES = [
    "ref_calumet_great_falls",
    "ref_cenex_laurel",
    "ref_par_billings",
    "ref_phillips_billings",
    "ref_silver_evanston",
    "ref_wyoming_newcastle",
]


def upsert_assignment(cur, variable_id, *, ts=None, formula=None, formula_inputs=None, notes=None):
    cur.execute(
        """
        INSERT INTO oil_network.variable_assignments
            (scenario_id, variable_id, effective_from, timeseries_id, formula,
             formula_inputs, notes)
        VALUES (%s, %s, '2015-01-01', %s, %s, %s, %s)
        ON CONFLICT (scenario_id, variable_id, effective_from) DO UPDATE
        SET timeseries_id  = EXCLUDED.timeseries_id,
            formula        = EXCLUDED.formula,
            formula_inputs = EXCLUDED.formula_inputs,
            notes          = EXCLUDED.notes
        """,
        (SCENARIO, variable_id, ts, formula, formula_inputs or [], notes),
    )


def upsert_var(cur, variable_id, variable_type, node_id, related_node_id=None):
    cur.execute(
        """
        INSERT INTO oil_network.variables
            (variable_id, variable_type, commodity, node_id, related_node_id, description)
        VALUES (%s, %s, 'crude', %s, %s, NULL)
        ON CONFLICT (variable_id) DO NOTHING
        """,
        (variable_id, variable_type, node_id, related_node_id),
    )


def upsert_scalar_vars(cur, node_id):
    cur.execute(
        """
        INSERT INTO oil_network.variables
            (variable_id, variable_type, commodity, node_id, related_node_id, description)
        VALUES
            ('production__crude__'    || %s, 'production',    'crude', %s, NULL, NULL),
            ('consumption__crude__'   || %s, 'consumption',   'crude', %s, NULL, NULL),
            ('inventory__crude__'     || %s, 'inventory',     'crude', %s, NULL, NULL),
            ('balancing_item__crude__'|| %s, 'balancing_item','crude', %s, NULL, NULL)
        ON CONFLICT (variable_id) DO NOTHING
        """,
        (node_id, node_id, node_id, node_id, node_id, node_id, node_id, node_id),
    )


# ---------------------------------------------------------------------------
# Step 1 — Create bakken_mt_gathering
# ---------------------------------------------------------------------------

def create_bakken_mt_gathering(cur):
    asset_id = "bakken_mt_gathering"
    cur.execute(
        """
        INSERT INTO oil_network.assets
            (asset_id, name, description, node_class, node_subtype, kind,
             starter_status, attributes)
        VALUES (%s, %s, %s, 'infrastructure', 'gathering', 'physical', 'collapsed', %s)
        ON CONFLICT (asset_id) DO NOTHING
        """,
        (
            asset_id,
            "Bakken-MT gathering network",
            "Bakken Montana gathering network (Williston Basin MT portion). Receives crude from bakken_mt and sends to PADD-4 refineries (Billings cluster, Calumet Great Falls, Wyoming refineries). Cross-state pooling with bakken_nd_gathering is modelled via the pipe_bakken_xstate connector. Collapsed in starter; activated when commercial gathering telemetry added.",
            Json({
                "function": "well_to_origin_terminal",
                "operator": "multiple_midstream",
                "transport_type": "small_diameter_gathering",
                "resolution_hierarchy": {"parent": "bakken", "aggregation": None},
            }),
        ),
    )
    cur.execute(
        f"""
        INSERT INTO oil_network.nodes (node_id, graph_id, asset_id, node_type, starter_status, attributes)
        VALUES (%s, {GRAPH_SQL}, %s, 'gathering', 'collapsed', %s)
        ON CONFLICT (node_id) DO NOTHING
        """,
        (asset_id, asset_id, Json({})),
    )
    upsert_scalar_vars(cur, asset_id)
    # inflow from bakken_mt
    upsert_var(cur, "inflow__crude__bakken_mt_gathering__bakken_mt", "inflow", asset_id, "bakken_mt")
    # outflows to 6 PADD-4 refineries
    for ref in PADD4_REFINERIES:
        upsert_var(cur, f"outflow__crude__bakken_mt_gathering__{ref}", "outflow", asset_id, ref)
    print(f"  ✓ {asset_id} (asset+node+{4 + 1 + 6} variables)")


# ---------------------------------------------------------------------------
# Step 2 — Create pipe_bakken_xstate connector
# ---------------------------------------------------------------------------

def create_pipe_bakken_xstate(cur):
    asset_id = "pipe_bakken_xstate"
    cur.execute(
        """
        INSERT INTO oil_network.assets
            (asset_id, name, description, node_class, node_subtype, kind,
             starter_status, attributes)
        VALUES (%s, %s, %s, 'infrastructure', 'pipeline', 'physical', 'collapsed', %s)
        ON CONFLICT (asset_id) DO NOTHING
        """,
        (
            asset_id,
            "Bakken cross-state gathering connector",
            "Conceptual aggregate of the cross-state midstream pipelines that link the ND and MT halves of the Bakken gathering network (Hess, Crestwood, Energy Transfer operate lines crossing the state border). Bidirectional flow carried by separate directed variables per Principle 2.12. All four flow variables are latent in the starter (no public TS measures inter-state Bakken gathering transfers); the constraint that ΣI=ΣO at the connector (pipeline pass-through defaults) is the Principle-2.11 latent allocation.",
            Json({
                "function": "inter_state_gathering_link",
                "operator": "multiple_midstream",
                "transport_type": "trunk_gathering",
                "bidirectional": True,
                "future_uses": ["per-operator decomposition", "per-grade decomposition"],
            }),
        ),
    )
    cur.execute(
        f"""
        INSERT INTO oil_network.nodes (node_id, graph_id, asset_id, node_type, starter_status, attributes)
        VALUES (%s, {GRAPH_SQL}, %s, 'pipeline', 'collapsed', %s)
        ON CONFLICT (node_id) DO NOTHING
        """,
        (asset_id, asset_id, Json({})),
    )
    upsert_scalar_vars(cur, asset_id)
    # Bidirectional: 2 inflows + 2 outflows on the connector
    upsert_var(cur, "inflow__crude__pipe_bakken_xstate__bakken_nd_gathering", "inflow", asset_id, "bakken_nd_gathering")
    upsert_var(cur, "inflow__crude__pipe_bakken_xstate__bakken_mt_gathering", "inflow", asset_id, "bakken_mt_gathering")
    upsert_var(cur, "outflow__crude__pipe_bakken_xstate__bakken_nd_gathering", "outflow", asset_id, "bakken_nd_gathering")
    upsert_var(cur, "outflow__crude__pipe_bakken_xstate__bakken_mt_gathering", "outflow", asset_id, "bakken_mt_gathering")
    # Paired variables on the two gatherings (mirror edges)
    upsert_var(cur, "outflow__crude__bakken_nd_gathering__pipe_bakken_xstate", "outflow", "bakken_nd_gathering", "pipe_bakken_xstate")
    upsert_var(cur, "outflow__crude__bakken_mt_gathering__pipe_bakken_xstate", "outflow", "bakken_mt_gathering", "pipe_bakken_xstate")
    upsert_var(cur, "inflow__crude__bakken_nd_gathering__pipe_bakken_xstate", "inflow", "bakken_nd_gathering", "pipe_bakken_xstate")
    upsert_var(cur, "inflow__crude__bakken_mt_gathering__pipe_bakken_xstate", "inflow", "bakken_mt_gathering", "pipe_bakken_xstate")
    print(f"  ✓ {asset_id} (asset+node+{4 + 4} connector vars + 4 gathering-side mirrors)")


# ---------------------------------------------------------------------------
# Step 3 — Variable assignments (TS + formula)
# ---------------------------------------------------------------------------

def assign_new_vars(cur):
    # Create variable rows on the OTHER endpoints (bakken_mt + 6 PADD-4 refineries).
    # Step 1 only created vars on bakken_mt_gathering itself; the paired/mirror
    # vars on the basin and the refineries also need to exist before assignments
    # can land (variable_assignments_check_graph trigger looks at v.node_id).
    upsert_var(cur, "outflow__crude__bakken_mt__bakken_mt_gathering",
               "outflow", "bakken_mt", "bakken_mt_gathering")
    for ref in PADD4_REFINERIES:
        upsert_var(cur, f"inflow__crude__{ref}__bakken_mt_gathering",
                   "inflow", ref, "bakken_mt_gathering")

    # bakken_mt now flows to bakken_mt_gathering
    upsert_assignment(
        cur, "outflow__crude__bakken_mt__bakken_mt_gathering",
        formula="production__crude__bakken_mt",
        formula_inputs=["production__crude__bakken_mt"],
        notes="Mirror of bakken_mt.production (state_sub_basin only outflow)",
    )
    upsert_assignment(
        cur, "inflow__crude__bakken_mt_gathering__bakken_mt",
        formula="outflow__crude__bakken_mt__bakken_mt_gathering",
        formula_inputs=["outflow__crude__bakken_mt__bakken_mt_gathering"],
        notes="Alias to paired outflow on bakken_mt (collapsed gathering)",
    )
    # 6 latent outflows from bakken_mt_gathering + paired refinery inflow mirrors
    for ref in PADD4_REFINERIES:
        upsert_assignment(
            cur, f"outflow__crude__bakken_mt_gathering__{ref}",
            formula="latent()",
            notes="Per-refinery split latent (Principle 2.11)",
        )
        upsert_assignment(
            cur, f"inflow__crude__{ref}__bakken_mt_gathering",
            formula=f"outflow__crude__bakken_mt_gathering__{ref}",
            formula_inputs=[f"outflow__crude__bakken_mt_gathering__{ref}"],
            notes="Mirror of paired outflow on bakken_mt_gathering (latent)",
        )
    # Connector — all 4 flow vars + 4 gathering-side mirrors all latent
    for vid in (
        "inflow__crude__pipe_bakken_xstate__bakken_nd_gathering",
        "inflow__crude__pipe_bakken_xstate__bakken_mt_gathering",
        "outflow__crude__pipe_bakken_xstate__bakken_nd_gathering",
        "outflow__crude__pipe_bakken_xstate__bakken_mt_gathering",
        "outflow__crude__bakken_nd_gathering__pipe_bakken_xstate",
        "outflow__crude__bakken_mt_gathering__pipe_bakken_xstate",
        "inflow__crude__bakken_nd_gathering__pipe_bakken_xstate",
        "inflow__crude__bakken_mt_gathering__pipe_bakken_xstate",
    ):
        upsert_assignment(
            cur, vid, formula="latent()",
            notes="Bidirectional Bakken cross-state link; latent per Principle 2.11 (no public TS)",
        )
    print("  ✓ 16 assignments created")


# ---------------------------------------------------------------------------
# Step 4 — Delete obsolete variables (old bakken_mt → bakken_nd_gathering route + 6 PADD-4 outflows on bakken_nd_gathering)
# ---------------------------------------------------------------------------

def delete_obsolete_vars(cur):
    obsolete = [
        # bakken_mt no longer flows to bakken_nd_gathering
        "outflow__crude__bakken_mt__bakken_nd_gathering",
        "inflow__crude__bakken_nd_gathering__bakken_mt",
        # 6 PADD-4 refineries no longer fed by bakken_nd_gathering
        *[f"outflow__crude__bakken_nd_gathering__{ref}" for ref in PADD4_REFINERIES],
        *[f"inflow__crude__{ref}__bakken_nd_gathering" for ref in PADD4_REFINERIES],
    ]
    # variable_assignments + scenario_resolved_values cascade on variable_id FK
    cur.execute(
        "DELETE FROM oil_network.variables WHERE variable_id = ANY(%s)",
        (obsolete,),
    )
    print(f"  ✓ {cur.rowcount} obsolete variables deleted (cascade clears assignments + resolved rows)")


# ---------------------------------------------------------------------------
# Step 5 — Inventory-child membership for all gathering nodes (gap.O artefact fix)
# ---------------------------------------------------------------------------

GATHERING_BY_PADD = {
    "padd2_view": ["bakken_nd_gathering"],
    "padd3_view": ["eagle_ford_gathering", "permian_nm_gathering", "permian_tx_gathering"],
    "padd4_view": ["bakken_mt_gathering"],
    "padd5_view": ["ans_gathering"],
}


def add_inventory_membership(cur):
    for view, gatherings in GATHERING_BY_PADD.items():
        new_inputs = [f"inventory__crude__{g}" for g in gatherings]
        inv_var = f"inventory__crude__{view}"
        # Preserve whatever recipe the override carries (TS or formula) and
        # extend only formula_inputs. padd_view.inventory is TS-bound to
        # eia:MCRSTP{N}1 (PADD-level stocks); we keep that and add gathering
        # nodes as same-type partition members.
        cur.execute(
            """
            SELECT timeseries_id, formula, formula_inputs, notes
            FROM oil_network.variable_assignments
            WHERE scenario_id = %s AND variable_id = %s
            """,
            (SCENARIO, inv_var),
        )
        row = cur.fetchone()
        if row is None:
            print(f"  ! no override row for {inv_var}; skipping (defaults-only)")
            continue
        ts, formula, existing_inputs, notes = row
        existing_inputs = list(existing_inputs or [])
        merged = list(dict.fromkeys(existing_inputs + new_inputs))
        added = [x for x in new_inputs if x not in existing_inputs]
        if not added:
            print(f"  · {inv_var}: already includes gathering nodes; skipping")
            continue
        # Append gathering-membership note
        suffix = " | gathering nodes added for partition-child membership (zero numerical effect; closes gap.O artefact)"
        new_notes = (notes or "") + suffix
        upsert_assignment(
            cur, inv_var, ts=ts, formula=formula, formula_inputs=merged,
            notes=new_notes,
        )
        print(f"  ✓ {inv_var}: +{added}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("=== Step 1: Create bakken_mt_gathering ===")
        create_bakken_mt_gathering(cur)
        print()
        print("=== Step 2: Create pipe_bakken_xstate connector ===")
        create_pipe_bakken_xstate(cur)
        print()
        print("=== Step 3: Variable assignments for new vars ===")
        assign_new_vars(cur)
        print()
        print("=== Step 4: Delete obsolete bakken_mt → bakken_nd_gathering + 6 PADD-4 outflows ===")
        delete_obsolete_vars(cur)
        print()
        print("=== Step 5: Inventory-child membership for 6 gathering nodes (gap.O fix) ===")
        add_inventory_membership(cur)
        conn.commit()
        print("\nDone. Re-run resolve_scenario.py + audit_partition_gaps.py to verify.")


if __name__ == "__main__":
    main()
