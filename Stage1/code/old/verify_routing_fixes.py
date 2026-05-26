"""Verify the routing fixes resolved the gaps. Print:
  - Reachability of every physical node to a refinery or export terminal
  - Wyoming / Colorado / Bakken / GoM / Oklahoma routing details
  - Refinery feed sources after fixes
  - Macro capacity vs production tally with the new refineries
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

with engine.connect() as c:
    nodes_df = pd.read_sql(text("""
        SELECT n.node_id, n.node_type, a.attributes->>'kind' AS kind
        FROM nodes n JOIN assets a ON a.asset_id=n.asset_id
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
for _, r in nodes_df.iterrows():
    G.add_node(r["node_id"], node_type=r["node_type"], kind=r["kind"])
G.add_edges_from(edges_df.itertuples(index=False, name=None))

print(f"Total: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
print(f"Physical: {sum(1 for n in G.nodes if G.nodes[n]['kind']=='physical')}, "
      f"Abstract: {sum(1 for n in G.nodes if G.nodes[n]['kind']=='abstract')}")

physical = [n for n in G.nodes if G.nodes[n]["kind"]=="physical"]
sinks    = [n for n in G.nodes if G.nodes[n]["node_type"] in ("export_terminal", "refinery")]
print(f"Sinks (refineries + exports): {len(sinks)}")

def reach(n):
    if n in sinks: return True
    try: return any(nx.has_path(G, n, s) for s in sinks)
    except nx.NodeNotFound: return False

unreach = [n for n in physical if not reach(n)]
print(f"\nPhysical nodes unable to reach a sink: {len(unreach)}")
for n in unreach:
    print(f"  {G.nodes[n]['node_type']:20s} {n}")

print("\n" + "="*100)
print("Out-edges from problem-child production nodes (after fixes)")
print("="*100)
for nid in ("wyoming_conventional", "colorado_conventional", "oklahoma_conventional",
            "bakken_nd_gathering", "gulf_of_america", "loop_terminal"):
    if nid not in G: continue
    out = list(G.successors(nid))
    print(f"\n{nid:30s}  ({G.nodes[nid]['node_type']})")
    print(f"  -> {out}")

print("\n" + "="*100)
print("Spot-check reachable refineries from each production source")
print("="*100)
def reachable_refineries(src):
    if src not in G: return []
    refineries = [n for n in G.nodes if G.nodes[n]["node_type"]=="refinery"]
    out = []
    for r in refineries:
        try:
            if nx.has_path(G, src, r):
                out.append(r)
        except nx.NodeNotFound:
            pass
    return sorted(out)

for src in ("permian_tx", "bakken_nd", "wyoming_conventional", "colorado_conventional",
            "oklahoma_conventional", "alaska_north_slope", "california_conventional",
            "gulf_of_america"):
    refs = reachable_refineries(src)
    print(f"\n{src:30s}  reaches {len(refs)} refineries:")
    for r in refs:
        print(f"    {r}")

print("\n" + "="*100)
print("Refinery feed sources (after fixes)")
print("="*100)
for n in sorted([n for n in G.nodes if G.nodes[n]["node_type"]=="refinery"]):
    feeders = list(G.predecessors(n))
    print(f"  {n:30s}  fed by: {feeders}")

# Macro check
print("\n" + "="*100)
print("Macro capacity / production tally")
print("="*100)
caps = {"ref_motiva_port_arthur":626,"ref_galveston_bay_mpc":593,"ref_exxon_baytown":561,
        "ref_marathon_garyville":597,"ref_citgo_lake_charles":419,"ref_bp_whiting":435,
        "ref_p66_wood_river":356,"ref_exxon_joliet":271,"ref_marathon_catlettsburg":291,
        "ref_pbf_toledo":170,"ref_marathon_la":363,"ref_chevron_richmond":245,
        "ref_bp_cherry_point":251,"ref_p66_bayway":258,"ref_pbf_delaware_city":190,
        "ref_salt_lake_cluster":180,"ref_hf_sinclair_wy":80,
        "ref_suncor_commerce_city":98,"ref_p66_ponca_city":210}
prod = {"permian_tx":4600,"permian_nm":1100,"bakken_nd":1100,"bakken_mt":60,
        "eagle_ford_tx":1200,"gulf_of_america":1800,"alaska_north_slope":420,
        "california_conventional":290,"oklahoma_conventional":400,
        "wyoming_conventional":270,"colorado_conventional":450}
print(f"  Modelled refining capacity: {sum(caps.values()):>6,} kbd "
      f"(was 5,886 kbd, +{sum(caps.values())-5886} kbd from new refineries)")
print(f"  Modelled production:        {sum(prod.values()):>6,} kbd")
print(f"  Real US refining: ~18,000 kbd. Real US production: ~13,000 kbd. Real exports: ~4,000 kbd.")
