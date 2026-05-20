"""Topology fixes from the 2026-05-20 routes audit.

Acts on the asset graph (variables table) so both starter_us_crude_2015_2025
and crude_starter_with_grades inherit the fix. CASCADE handles assignments
and scenario_resolved_values rows automatically.

Fixes:
  1. Seaway: drop the wrong-direction Houston->Seaway->Cushing edges
     (reversal completed 2012; line runs only Cushing->Houston).
  2. LOCAP: reverse — LOCAP runs LOOP->St James, not Houston->LOOP.
  3. DAPL/ETCO: drop the spurious DAPL->Cushing terminus and add the
     Bakken-to-Gulf trunk continuation (pipe_dapl_etco->nederland_hub).
  4. BP Cherry Point identity: asset name + location were Phillips 66
     Ferndale's; fix to BP Cherry Point (the asset_id we use).
  5. Add Keystone Phase 1 delivery: pipe_keystone->patoka_hub.
  6. Add TMX delivery to BP Cherry Point.
  7. Add Express-Platte delivery to HF Sinclair WY.

Audit sources: Enterprise Products / RBN Energy (Seaway), PHMSA + GEM wiki
(LOCAP), GEM wiki / RBN Energy (DAPL/ETCO), Salish Current / Trans Mountain
docs (TMX -> Washington refineries), Britannica / TC Energy (Keystone P1
endpoint).
"""
from __future__ import annotations
import psycopg2
from psycopg2.extras import execute_values

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
SCENARIOS = ("starter_us_crude_2015_2025", "crude_starter_with_grades")
COMMODITY = "crude"
EFFECTIVE_FROM = "2015-01-01"


def edge_var_ids(src, dst, c=COMMODITY):
    """The two variable_ids for a directed edge src->dst on commodity c."""
    return (f"outflow__{c}__{src}__{dst}", f"inflow__{c}__{dst}__{src}")


def drop_edges(cur, pairs):
    ids = []
    for s, d in pairs:
        ids.extend(edge_var_ids(s, d))
    cur.execute("DELETE FROM oil_network.variables WHERE variable_id = ANY(%s)", (ids,))
    return cur.rowcount


def add_edges(cur, pairs, formula="latent()"):
    """Insert outflow/inflow variable pair + latent() assignments for both scenarios."""
    var_rows = []
    for s, d in pairs:
        oid, iid = edge_var_ids(s, d)
        # (variable_id, variable_type, commodity, node_id, related_node_id, attributes)
        var_rows.append((oid, "outflow", COMMODITY, s, d, "{}"))
        var_rows.append((iid, "inflow",  COMMODITY, d, s, "{}"))
    execute_values(cur,
        "INSERT INTO oil_network.variables (variable_id, variable_type, commodity, node_id, related_node_id, attributes) "
        "VALUES %s ON CONFLICT DO NOTHING",
        var_rows)
    n_vars = cur.rowcount

    asn_rows = []
    for s, d in pairs:
        oid, iid = edge_var_ids(s, d)
        for sc in SCENARIOS:
            asn_rows.append((sc, oid, EFFECTIVE_FROM, None, formula, [], None))
            asn_rows.append((sc, iid, EFFECTIVE_FROM, None, formula, [], None))
    execute_values(cur,
        "INSERT INTO oil_network.variable_assignments "
        "(scenario_id, variable_id, effective_from, timeseries_id, formula, formula_inputs, notes) "
        "VALUES %s ON CONFLICT DO NOTHING",
        asn_rows)
    return n_vars, cur.rowcount


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:

        # 1. Seaway — drop wrong-direction Houston↔Cushing leg via pipe_seaway
        n = drop_edges(cur, [("houston_hub", "pipe_seaway"), ("pipe_seaway", "cushing_hub")])
        print(f"[1] Seaway: deleted {n} wrong-direction variables")

        # 2. LOCAP — reverse to LOOP->St James
        n = drop_edges(cur, [("houston_hub", "pipe_locap"), ("pipe_locap", "loop_terminal")])
        nv, na = add_edges(cur, [("loop_terminal", "pipe_locap"), ("pipe_locap", "st_james_hub")])
        print(f"[2] LOCAP: deleted {n} wrong + added {nv} new vars / {na} assignments")

        # 3. DAPL/ETCO — drop Cushing terminus, add Nederland continuation
        n = drop_edges(cur, [("pipe_dapl_etco", "cushing_hub")])
        nv, na = add_edges(cur, [("pipe_dapl_etco", "nederland_hub")])
        print(f"[3] DAPL/ETCO: deleted {n} wrong + added {nv} new vars / {na} assignments")

        # 4. BP Cherry Point identity fix
        cur.execute("""
            UPDATE oil_network.assets
            SET name = 'BP CHERRY POINT REFINERY'
            WHERE asset_id = 'ref_bp_cherry_point'
        """)
        cur.execute("""
            UPDATE oil_network.locations
            SET name = 'BP CHERRY POINT REFINERY',
                lat = 48.870, lon = -122.730
            WHERE location_id = 'loc__ref_bp_cherry_point'
        """)
        print("[4] BP Cherry Point: renamed asset + location, corrected coords (48.870, -122.730)")

        # 5. Keystone Phase 1 delivery to Patoka
        nv, na = add_edges(cur, [("pipe_keystone", "patoka_hub")])
        print(f"[5] Keystone->Patoka: added {nv} vars / {na} assignments")

        # 6. TMX -> BP Cherry Point
        nv, na = add_edges(cur, [("pipe_trans_mountain_tmx", "ref_bp_cherry_point")])
        print(f"[6] TMX->BP Cherry Point: added {nv} vars / {na} assignments")

        # 7. Express-Platte -> HF Sinclair WY
        nv, na = add_edges(cur, [("pipe_express_platte", "ref_hf_sinclair_wy")])
        print(f"[7] Express-Platte->HF Sinclair WY: added {nv} vars / {na} assignments")

        conn.commit()

        print()
        print("Post-fix verify:")
        for label, sql in [
            ("Seaway edges (should be 4: cushing->seaway->houston)",
             "SELECT node_id, related_node_id, variable_type FROM oil_network.variables "
             "WHERE pipe_seaway IS NOT NULL AND (node_id='pipe_seaway' OR related_node_id='pipe_seaway') "
             "AND related_node_id IS NOT NULL ORDER BY variable_type, node_id"),
        ]:
            pass
        for q in [
            "SELECT node_id, related_node_id FROM oil_network.variables WHERE variable_type='outflow' AND (node_id='pipe_seaway' OR related_node_id='pipe_seaway')",
            "SELECT node_id, related_node_id FROM oil_network.variables WHERE variable_type='outflow' AND (node_id='pipe_locap' OR related_node_id='pipe_locap')",
            "SELECT node_id, related_node_id FROM oil_network.variables WHERE variable_type='outflow' AND (node_id='pipe_dapl_etco' OR related_node_id='pipe_dapl_etco')",
            "SELECT node_id, related_node_id FROM oil_network.variables WHERE variable_type='outflow' AND node_id='pipe_keystone'",
            "SELECT node_id, related_node_id FROM oil_network.variables WHERE variable_type='outflow' AND node_id='pipe_trans_mountain_tmx'",
            "SELECT node_id, related_node_id FROM oil_network.variables WHERE variable_type='outflow' AND node_id='pipe_express_platte' AND related_node_id='ref_hf_sinclair_wy'",
        ]:
            cur.execute(q)
            rows = cur.fetchall()
            print(f"  {q.split('AND')[1].strip()[:70]}: {len(rows)} rows  {rows[:6]}{'...' if len(rows)>6 else ''}")


if __name__ == "__main__":
    main()
