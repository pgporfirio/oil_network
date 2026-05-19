"""Structural mass-balance / capacity-feasibility check on the asset graph.

Without EIA timeseries we can only check capacity-level feasibility:
  - Does every refinery have enough notional source crude reachable to fill its capacity?
  - Does every production source have a downstream sink chain with enough capacity to absorb it?
  - Where are the bottlenecks?
"""
import pandas as pd
import networkx as nx
from sqlalchemy import create_engine, text

pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", None)

engine = create_engine(
    "postgresql+psycopg2://eia_user:eia_password@localhost:5432/eia_crude",
    connect_args={"options": "-csearch_path=oil_network,public"}, future=True,
)

# Build the directed flow graph.
with engine.connect() as c:
    nodes_df = pd.read_sql(text("""
        SELECT n.node_id, n.node_type, a.attributes->>'kind' AS kind,
               a.attributes->'configuration' AS configuration,
               l.padd
        FROM nodes n JOIN assets a ON a.asset_id=n.asset_id
        JOIN locations l ON l.location_id=a.location_id
        WHERE n.graph_id='us_crude_starter'
    """), c)
    edges_df = pd.read_sql(text("""
        SELECT v.node_id AS source, v.related_node_id AS target
        FROM variables v
        JOIN nodes ns ON ns.node_id=v.node_id         AND ns.graph_id='us_crude_starter'
        JOIN nodes nt ON nt.node_id=v.related_node_id AND nt.graph_id='us_crude_starter'
        WHERE v.variable_type='outflow' AND v.commodity='crude'
    """), c)

G = nx.DiGraph()
node_attr = nodes_df.set_index("node_id").to_dict("index")
for nid, row in node_attr.items():
    G.add_node(nid, **row)
G.add_edges_from(edges_df.itertuples(index=False, name=None))

PROD_KBD = {
    "permian_tx":4600, "permian_nm":1100, "bakken_nd":1100, "bakken_mt":60,
    "eagle_ford_tx":1200, "gulf_of_america":1800, "alaska_north_slope":420,
    "california_conventional":290, "oklahoma_conventional":400,
    "wyoming_conventional":270, "colorado_conventional":450,
}
EXPORT_TERM_KBD = {
    "ingleside_export":1800, "houston_export":1200, "nederland_export":1100,
    "loop_terminal":1500,  # LOOP is also an export terminal in our model
}

# Pull refinery and pipeline capacities directly from configuration attributes.
def cap_of(nid, key="capacity_bd"):
    cfg = node_attr[nid]["configuration"] or {}
    return cfg.get(key) or cfg.get("capacity_bpd")

REF_KBD = {n: int(cap_of(n)/1000) for n in G.nodes if node_attr[n]["node_type"]=="refinery" and cap_of(n)}

# ---------------------------------------------------------------------------
# 1. PADD-level regional balance: production vs. local refining vs. needs imports / exports
# ---------------------------------------------------------------------------
print("="*100)
print("PADD-LEVEL REGIONAL CAPACITY BALANCE (kbd)")
print("="*100)

prod_by_padd, refcap_by_padd, exp_by_padd = {}, {}, {}
for nid, p in PROD_KBD.items():
    padd = node_attr[nid]["padd"]
    prod_by_padd[padd] = prod_by_padd.get(padd, 0) + p
for n, cap in REF_KBD.items():
    padd = node_attr[n]["padd"]
    refcap_by_padd[padd] = refcap_by_padd.get(padd, 0) + cap
for nid, cap in EXPORT_TERM_KBD.items():
    padd = node_attr[nid]["padd"]
    exp_by_padd[padd] = exp_by_padd.get(padd, 0) + cap

print(f"  {'PADD':6s} {'Prod':>8s} {'Refining':>10s} {'Export cap':>12s} {'Net (P-R-E)':>14s}    Note")
total_p, total_r, total_e = 0, 0, 0
for padd in sorted(set(list(prod_by_padd) + list(refcap_by_padd) + list(exp_by_padd))):
    p = prod_by_padd.get(padd, 0)
    r = refcap_by_padd.get(padd, 0)
    e = exp_by_padd.get(padd, 0)
    net = p - r - e
    total_p, total_r, total_e = total_p + p, total_r + r, total_e + e
    if net > 0:    note = f"surplus {net:>5} kbd -> needs export or out-of-PADD pipeline"
    elif net < 0:  note = f"deficit {-net:>5} kbd -> needs imports or in-bound pipeline"
    else:          note = "balanced"
    print(f"  {padd:6s} {p:>8,} {r:>10,} {e:>12,} {net:>+14,}    {note}")
print(f"  {'TOTAL':6s} {total_p:>8,} {total_r:>10,} {total_e:>12,} {(total_p-total_r-total_e):>+14,}")
print("  (real US ~ 13,000 prod / ~18,000 refining / ~4,000 export — ours undermodels refining and over/under exports)")

# ---------------------------------------------------------------------------
# 2. Per-refinery: feed sources + reachable production
# ---------------------------------------------------------------------------
print("\n" + "="*100)
print("PER-REFINERY CAPACITY vs REACHABLE PRODUCTION")
print("="*100)
print(f"  {'Refinery':32s} {'Cap':>5s} {'Feeders':40s} {'Reachable prod (kbd)':>22s}")

# For each refinery, for each production node, check reachability and sum production.
prod_nodes = list(PROD_KBD)
for ref in sorted(REF_KBD, key=lambda n: -REF_KBD[n]):
    feeders = list(G.predecessors(ref))
    reachable_prod = 0
    sources_reach = []
    for p in prod_nodes:
        try:
            if nx.has_path(G, p, ref):
                reachable_prod += PROD_KBD[p]
                sources_reach.append(p)
        except nx.NodeNotFound:
            pass
    feeders_short = ",".join(f.replace("ref_","").replace("_conventional","").replace("_hub","")[:14] for f in feeders[:3])
    print(f"  {ref:32s} {REF_KBD[ref]:>5d} {feeders_short:40s} {reachable_prod:>22,}")

# ---------------------------------------------------------------------------
# 3. Alaska closed-system check
# ---------------------------------------------------------------------------
print("\n" + "="*100)
print("ALASKA CLOSED-SYSTEM CHECK")
print("="*100)
print("  Production: alaska_north_slope = 420 kbd")
print("  Routing: alaska_north_slope -> ans_gathering -> pipe_taps (cap ~ 2,100 kbd) -> valdez_origin")
print("  From valdez_origin three destinations:")
for tgt in G.successors("valdez_origin"):
    print(f"    -> {tgt}")
print()
west_coast_refs = [n for n in G.nodes if node_attr[n]["padd"]=="PADD5" and node_attr[n]["node_type"]=="refinery"]
print(f"  West Coast refining demand: {sum(REF_KBD[r] for r in west_coast_refs):,} kbd")
print(f"     Marathon LA: {REF_KBD['ref_marathon_la']:,} kbd")
print(f"     Chevron Richmond: {REF_KBD['ref_chevron_richmond']:,} kbd")
print(f"     BP Cherry Point: {REF_KBD['ref_bp_cherry_point']:,} kbd")
print(f"  West Coast supply: AK (420) + CA (290) = 710 kbd domestic")
print(f"  Import requirement: {sum(REF_KBD[r] for r in west_coast_refs) - 710:,} kbd (must come from padd5_imports_agg)")
print(f"  Conclusion: Alaska is NOT a closed system — it shares West Coast refining with CA + foreign imports.")

# ---------------------------------------------------------------------------
# 4. Identify bottlenecks: refineries with very few feeders, sources with limited routes
# ---------------------------------------------------------------------------
print("\n" + "="*100)
print("STRUCTURAL BOTTLENECKS")
print("="*100)
print("\nRefineries with only one feeder (single point of failure):")
for ref in sorted(REF_KBD):
    feeders = list(G.predecessors(ref))
    if len(feeders) == 1:
        print(f"  {ref:30s}  cap={REF_KBD[ref]:>5} kbd  fed only by: {feeders[0]}")

print("\nProduction nodes with only one outflow path (might create congestion):")
for p in prod_nodes:
    out = list(G.successors(p))
    if len(out) == 1:
        print(f"  {p:30s}  prod={PROD_KBD[p]:>5} kbd  -> {out[0]}")

# ---------------------------------------------------------------------------
# 5. Per-physical-node net flow degree (sanity)
# ---------------------------------------------------------------------------
print("\n" + "="*100)
print("PER-PHYSICAL-NODE FLOW SUMMARY (in-degree, out-degree, role)")
print("="*100)
print(f"  {'Node':32s} {'Type':22s} {'In':>3s} {'Out':>3s}  Role")
for nid in sorted(G.nodes):
    if node_attr[nid]["kind"] != "physical":
        continue
    nt   = node_attr[nid]["node_type"]
    ind  = G.in_degree(nid)
    outd = G.out_degree(nid)
    if   ind == 0 and outd >  0: role = "SOURCE"
    elif ind >  0 and outd == 0: role = "SINK"
    elif ind == 0 and outd == 0: role = "isolated"
    else:                        role = "transit"
    print(f"  {nid:32s} {nt:22s} {ind:>3d} {outd:>3d}  {role}")
