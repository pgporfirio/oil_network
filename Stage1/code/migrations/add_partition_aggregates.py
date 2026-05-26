"""Extend the aggregate pattern to remaining partition gaps.

Builds three families of physical-layer aggregates that move TS authority off
the observational PADD-view nodes and onto child aggregate nodes, so each
formula-input chain becomes same-type and the partition audit picks them up
as boundary contributors.

Families:

  1. Canadian imports (PADDs 1, 3)            -> `padd{1,3}_canadian_imports_agg`
     - mirrors the `padd2_canadian_imports_agg` prototype from 2026-05-13.
     - node_type = import_terminal (pass-through scalars from defaults)
     - TS authority: agg.inflow_from_canadian_oil_sands
     - re-point: padd_N_view.inflow_from_canadian aliases agg.inflow

  2. Exports (PADDs 1..5)                     -> `padd{1..5}_exports_agg`
     - node_type = export_terminal
     - TS authority: agg.outflow_to_foreign_export_destination
     - re-point: padd_N_view.outflow_to_foreign aliases agg.outflow

  3. Inter-PADD corridors (12 directional)    -> `inter_padd_{A}_to_{B}_agg`
     - node_type = pipeline
     - TS authority: agg.outflow_to_padd_B_view
     - cross-type internal alias: agg.inflow_from_padd_A = alias(agg.outflow_to_padd_B)
       so the agg's inflow side resolves to the same kbd value (Pattern 2.11 -
       mass balance closes at the aggregate level: in = out for a pipe).
     - re-points BOTH ends:
         padd_A_view.outflow_to_padd_B aliases agg.outflow_to_padd_B  (closes A's gap.O)
         padd_B_view.inflow_from_padd_A aliases agg.inflow_from_padd_A (closes B's gap.I)
     - existing pipe-constituent formula_inputs (3 corridors from
       `add_inter_padd_pipe_constituents.py`) are migrated onto the new
       agg.outflow_to_padd_B variable so the Principle 2.11 latent-allocation
       documentation is preserved.

PADD 4 & 5 Canadian inflows are handled separately by
`repoint_canadian_via_cross_border_pipes.py` (alias to the existing
cross-border pipe's inflow_from_canadian, no new aggregate node needed
since the pipe is already the physical aggregate).

The new aggregates become partition children of the appropriate PADD view
via SAME-TYPE formula_inputs references, so `audit_partition_gaps.py`
recognises them as boundary contributors.

Idempotent: re-running upserts to the same state. Reversible by deleting
the new asset+node+variables and restoring the prior assignment formulas.

After running this script, re-run:
  python resolve_scenario.py
  python audit_ts_binding_uniqueness.py
  python audit_partition_gaps.py
"""

from __future__ import annotations

import sys
from typing import Optional

import psycopg2
from psycopg2.extras import Json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIO = "starter_us_crude_2015_2025"
GRAPH_SQL = "(SELECT graph_id FROM oil_network.graphs LIMIT 1)"


# ---------------------------------------------------------------------------
# Aggregate configurations
# ---------------------------------------------------------------------------

CANADIAN_IMPORTS = [
    # (padd_n, ts_id)
    (1, "eia:MCRIPP1CA2"),
    (3, "eia:MCRIPP3CA2"),
]

EXPORTS = [
    # (padd_n, ts_id)
    (1, "eia:MCREXP12"),
    (2, "eia:MCREXP22"),
    (3, "eia:MCREXP32"),
    (4, "eia:MCREXP42"),
    (5, "eia:MCREXP52"),
]

INTER_PADD = [
    # (src_padd, dst_padd, ts_id)
    (1, 2, "eia:combined_inter_padd_P1_to_P2_kbd"),
    (1, 3, "eia:combined_inter_padd_P1_to_P3_kbd"),
    (2, 1, "eia:combined_inter_padd_P2_to_P1_kbd"),
    (2, 3, "eia:combined_inter_padd_P2_to_P3_kbd"),
    (2, 4, "eia:combined_inter_padd_P2_to_P4_kbd"),
    (3, 1, "eia:combined_inter_padd_P3_to_P1_kbd"),
    (3, 2, "eia:combined_inter_padd_P3_to_P2_kbd"),
    (3, 4, "eia:combined_inter_padd_P3_to_P4_kbd"),
    (3, 5, "eia:combined_inter_padd_P3_to_P5_kbd"),
    (4, 2, "eia:combined_inter_padd_P4_to_P2_kbd"),
    (4, 3, "eia:combined_inter_padd_P4_to_P3_kbd"),
    (5, 3, "eia:combined_inter_padd_P5_to_P3_kbd"),
]


# ---------------------------------------------------------------------------
# Generic agg-creation helpers
# ---------------------------------------------------------------------------

def upsert_asset(cur, asset_id: str, name: str, description: str,
                  node_subtype: str, attributes: dict) -> None:
    cur.execute(
        """
        INSERT INTO oil_network.assets
            (asset_id, name, description, node_class, node_subtype, kind,
             starter_status, attributes)
        VALUES (%s, %s, %s, 'infrastructure', %s, 'physical', 'authoritative', %s)
        ON CONFLICT (asset_id) DO NOTHING
        """,
        (asset_id, name, description, node_subtype, Json(attributes)),
    )


def upsert_node(cur, node_id: str, node_type: str) -> None:
    cur.execute(
        f"""
        INSERT INTO oil_network.nodes
            (node_id, graph_id, asset_id, node_type, starter_status, attributes)
        VALUES (%s, {GRAPH_SQL}, %s, %s, 'authoritative', %s)
        ON CONFLICT (node_id) DO NOTHING
        """,
        (node_id, node_id, node_type, Json({})),
    )


def upsert_scalar_vars(cur, node_id: str) -> None:
    """Insert P, C, S, B variables for a pass-through node.

    Numerical values come from node_type_default_formulas via
    v_effective_assignments, but the variable rows must exist in
    `variables` for the resolver to dispatch them.
    """
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


def upsert_relational_var(cur, node_id: str, related_node_id: str,
                            variable_type: str) -> str:
    """Insert one inflow/outflow variable row; return the variable_id."""
    vid = f"{variable_type}__crude__{node_id}__{related_node_id}"
    cur.execute(
        """
        INSERT INTO oil_network.variables
            (variable_id, variable_type, commodity, node_id, related_node_id, description)
        VALUES (%s, %s, 'crude', %s, %s, NULL)
        ON CONFLICT (variable_id) DO NOTHING
        """,
        (vid, variable_type, node_id, related_node_id),
    )
    return vid


def upsert_assignment(cur, variable_id: str, *, ts: Optional[str] = None,
                       formula: Optional[str] = None,
                       formula_inputs: Optional[list[str]] = None,
                       notes: Optional[str] = None) -> None:
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


def repoint_to_alias(cur, view_var: str, target_var: str, note: str) -> None:
    """Re-point an existing assignment to alias `target_var`.

    Sets formula = target_var (bare variable_id, the resolver's rule-6 form)
    and formula_inputs = [target_var] (so the audit's same-type children
    query picks up the relation).
    """
    cur.execute(
        """
        UPDATE oil_network.variable_assignments
        SET timeseries_id = NULL,
            formula = %s,
            formula_inputs = ARRAY[%s]::text[],
            notes = COALESCE(NULLIF(notes,''),'') ||
                    CASE WHEN COALESCE(notes,'') = '' THEN %s ELSE ' | ' || %s END
        WHERE scenario_id = %s AND variable_id = %s
        """,
        (target_var, target_var, note, note, SCENARIO, view_var),
    )


# ---------------------------------------------------------------------------
# Family 1: Canadian imports
# ---------------------------------------------------------------------------

def build_canadian_imports(cur) -> None:
    for n, ts in CANADIAN_IMPORTS:
        agg_id = f"padd{n}_canadian_imports_agg"
        view_id = f"padd{n}_view"
        upsert_asset(
            cur, agg_id,
            f"PADD {n} Canadian imports aggregate",
            f"Abstract aggregate node that receives Canadian crude flowing into PADD {n}. "
            "Holds the PADD-aggregate TS so the per-pipe split stays a Principle-2.11 "
            "latent allocation. Future home for per-grade decomposition.",
            "import_terminal",
            {
                "operator": "multiple",
                "purpose": f"PADD-{n}-aggregate TS holder for Canadian imports",
                "future_grades": ["heavy_sour_dilbit", "light_synthetic", "medium"],
            },
        )
        upsert_node(cur, agg_id, "import_terminal")
        upsert_scalar_vars(cur, agg_id)
        agg_inflow = upsert_relational_var(cur, agg_id, "canadian_oil_sands", "inflow")
        agg_outflow = upsert_relational_var(cur, agg_id, view_id, "outflow")

        upsert_assignment(
            cur, agg_inflow, ts=ts,
            notes=f"PADD-{n} Canadian imports authoritative on this aggregate; padd{n}_view alias-derived",
        )
        upsert_assignment(
            cur, agg_outflow, formula="latent()",
            notes=f"Pass-through to padd{n}_view; latent (no downstream consumer needs it resolved for partition closure)",
        )

        # Re-point the existing padd_view variable
        view_var = f"inflow__crude__{view_id}__canadian_oil_sands"
        repoint_to_alias(
            cur, view_var, agg_inflow,
            f"repointed: TS moved to {agg_id}, this now alias-derived",
        )

        print(f"  ✓ {agg_id} (TS {ts}); padd{n}_view.inflow_from_canadian -> alias")


# ---------------------------------------------------------------------------
# Family 2: Exports
# ---------------------------------------------------------------------------

def build_exports(cur) -> None:
    for n, ts in EXPORTS:
        agg_id = f"padd{n}_exports_agg"
        view_id = f"padd{n}_view"
        upsert_asset(
            cur, agg_id,
            f"PADD {n} exports aggregate",
            f"Abstract aggregate node holding the PADD-{n} crude exports TS. "
            "Sits between the PADD-{n} view and the foreign_export_destination "
            "boundary sink. Future home for per-destination decomposition.",
            "export_terminal",
            {
                "operator": "multiple",
                "purpose": f"PADD-{n}-aggregate TS holder for crude exports",
                "future_destinations": ["asia", "europe", "latin_america"],
            },
        )
        upsert_node(cur, agg_id, "export_terminal")
        upsert_scalar_vars(cur, agg_id)
        agg_inflow = upsert_relational_var(cur, agg_id, view_id, "inflow")
        agg_outflow = upsert_relational_var(cur, agg_id, "foreign_export_destination", "outflow")

        upsert_assignment(
            cur, agg_outflow, ts=ts,
            notes=f"PADD-{n} crude exports authoritative on this aggregate; padd{n}_view alias-derived",
        )
        upsert_assignment(
            cur, agg_inflow, formula="latent()",
            notes=f"Receipt from padd{n}_view; latent (mirror placeholder, no downstream consumer)",
        )

        # Re-point the existing padd_view variable
        view_var = f"outflow__crude__{view_id}__foreign_export_destination"
        repoint_to_alias(
            cur, view_var, agg_outflow,
            f"repointed: TS moved to {agg_id}, this now alias-derived",
        )

        print(f"  ✓ {agg_id} (TS {ts}); padd{n}_view.outflow_to_foreign -> alias")


# ---------------------------------------------------------------------------
# Family 3: Inter-PADD corridors
# ---------------------------------------------------------------------------

# Pipe-constituent metadata from add_inter_padd_pipe_constituents.py.
# Moved onto the new agg.outflow variable so the Principle 2.11 decomposition
# documentation is preserved in its natural home.
INTER_PADD_PIPE_CONSTITUENTS = {
    (2, 3): [
        "outflow__crude__pipe_capline__st_james_hub",
        "outflow__crude__pipe_marketlink__nederland_hub",
        "outflow__crude__pipe_seaway__houston_hub",
    ],
    (3, 2): [
        "outflow__crude__pipe_basin__cushing_hub",
        "outflow__crude__pipe_seaway__cushing_hub",
    ],
    (4, 2): [
        "outflow__crude__pipe_pony_express__cushing_hub",
    ],
}


def build_inter_padd(cur) -> None:
    for a, b, ts in INTER_PADD:
        agg_id = f"inter_padd_{a}_to_{b}_agg"
        src_view = f"padd{a}_view"
        dst_view = f"padd{b}_view"
        pipes = INTER_PADD_PIPE_CONSTITUENTS.get((a, b), [])

        upsert_asset(
            cur, agg_id,
            f"Inter-PADD {a}->{b} aggregate",
            f"Abstract aggregate node representing the bundle of physical pipelines "
            f"(and tanker movements where applicable) carrying crude from PADD {a} to "
            f"PADD {b}. Holds the inter-PADD TS so per-pipe splits stay a Principle-2.11 "
            "latent allocation. Becomes a partition child of BOTH end-PADDs via "
            "same-type aliases, closing gap.O on the sender and gap.I on the receiver.",
            "pipeline",
            {
                "operator": "multiple",
                "purpose": f"PADD-{a}-to-PADD-{b} inter-PADD aggregate",
                "modeled_pipe_constituents": pipes if pipes else "none (tanker/rail or unmodeled)",
            },
        )
        upsert_node(cur, agg_id, "pipeline")
        upsert_scalar_vars(cur, agg_id)
        agg_inflow = upsert_relational_var(cur, agg_id, src_view, "inflow")
        agg_outflow = upsert_relational_var(cur, agg_id, dst_view, "outflow")

        # TS on outflow (matching the existing convention).
        upsert_assignment(
            cur, agg_outflow, ts=ts,
            formula_inputs=pipes,  # preserved pipe-constituent metadata (Principle 2.11)
            notes=(
                f"PADD-{a}->PADD-{b} TS authoritative on this aggregate; both "
                "padd_views alias-derived. "
                + (f"Constrained by Σ of {len(pipes)} modelled pipe(s) (latent split)."
                   if pipes else "")
            ),
        )
        # Cross-type internal alias: inflow side resolves to outflow side (mass
        # balance: ΣI=ΣO for a pipeline, P=C=ΔS=B=0). This makes the receiver-side
        # alias chain same-type (padd_B.inflow_from_A aliases agg.inflow_from_A).
        upsert_assignment(
            cur, agg_inflow,
            formula=agg_outflow,
            formula_inputs=[agg_outflow],
            notes=(
                "Cross-type internal alias to agg's outflow (mass-balance closure "
                "for pipeline pass-through: ΣI=ΣO). Lets the receiver PADD's "
                "inflow alias a same-type variable for partition-child detection."
            ),
        )

        # Re-point sender PADD's outflow
        src_var = f"outflow__crude__{src_view}__{dst_view}"
        repoint_to_alias(
            cur, src_var, agg_outflow,
            f"repointed: TS moved to {agg_id}, this now alias-derived",
        )
        # Re-point receiver PADD's inflow
        dst_var = f"inflow__crude__{dst_view}__{src_view}"
        repoint_to_alias(
            cur, dst_var, agg_inflow,
            f"repointed: now aliases {agg_id} inflow (same-type partition-child chain)",
        )

        pipe_note = f" + {len(pipes)} pipe constituents" if pipes else ""
        print(f"  ✓ {agg_id} (TS {ts}){pipe_note}; both padd_views re-aliased")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Family 4: PADD 4/5 Canadian via existing cross-border pipes
# ---------------------------------------------------------------------------
# The cross-border pipes pipe_express_platte and pipe_trans_mountain_tmx are
# themselves physical-layer aggregates that already carry the Canadian TS on
# canadian_oil_sands.outflow_to_pipe. The pipe has a reverse-mirrored
# `inflow_from_canadian_oil_sands` variable on its OWN side. Re-pointing the
# padd_view alias to that variable (same-type=inflow, same-rel=canadian_oil_sands)
# makes the pipe a partition child of the padd_view for inflow purposes,
# closing the corresponding gap.I without creating a redundant aggregate node.

CROSS_BORDER_PIPE_CANADIAN = [
    # (padd_n, pipe_node_id)
    (4, "pipe_express_platte"),
    (5, "pipe_trans_mountain_tmx"),
]


def repoint_padd45_canadian(cur) -> None:
    for n, pipe in CROSS_BORDER_PIPE_CANADIAN:
        view_var = f"inflow__crude__padd{n}_view__canadian_oil_sands"
        pipe_inflow = f"inflow__crude__{pipe}__canadian_oil_sands"

        # Ensure the pipe's inflow variable exists and is reverse-mirror-resolvable
        cur.execute(
            "SELECT variable_id FROM oil_network.variables WHERE variable_id = %s",
            (pipe_inflow,),
        )
        if not cur.fetchone():
            print(f"  ! skipping PADD {n}: {pipe_inflow} does not exist")
            continue

        # Ensure the pipe's inflow has an assignment; if not, create one that
        # reverse-mirrors from canadian_oil_sands.outflow_to_pipe.
        canadian_outflow = f"outflow__crude__canadian_oil_sands__{pipe}"
        cur.execute(
            "SELECT 1 FROM oil_network.variable_assignments WHERE scenario_id = %s AND variable_id = %s",
            (SCENARIO, pipe_inflow),
        )
        if not cur.fetchone():
            upsert_assignment(
                cur, pipe_inflow, formula=canadian_outflow,
                formula_inputs=[canadian_outflow],
                notes=f"Reverse-mirror alias to {canadian_outflow} so padd{n}_view.inflow has a same-type partition-child target",
            )

        repoint_to_alias(
            cur, view_var, pipe_inflow,
            f"repointed: now aliases {pipe}.inflow_from_canadian (same-type partition-child chain via cross-border pipe)",
        )
        print(f"  ✓ padd{n}_view.inflow_from_canadian -> alias({pipe_inflow})")


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("=== Building Canadian imports aggregates (PADD 1, 3) ===")
        build_canadian_imports(cur)
        print()
        print("=== Re-pointing PADD 4, 5 Canadian via existing cross-border pipes ===")
        repoint_padd45_canadian(cur)
        print()
        print("=== Building exports aggregates (PADD 1..5) ===")
        build_exports(cur)
        print()
        print("=== Building inter-PADD corridor aggregates (12 directional) ===")
        build_inter_padd(cur)

        conn.commit()
        print("\nDone. Re-run resolve_scenario.py + audit_partition_gaps.py to verify.")


if __name__ == "__main__":
    main()
