"""
Source -> sink reachability audit with real-world reasonableness checks.

For every source (production node + import node) the script walks the live
flow graph and lists the sinks (refinery + export terminal) it can reach.
Each (source, sink_region) pair is flagged against a real-world rule set:

  yes      : source SHOULD reach this region; we DO reach it (correct)
  miss     : source SHOULD reach this region; we DON'T reach it (under-routed)
  unexp    : source should NOT reach this region; we DO reach it (over-routed)
  ok-skip  : source should NOT reach this region; we DON'T reach it (correct)

The expectations are conservative: rules cover well-established public flows.
Edge cases (rail, marine cabotage outside our model) are noted in comments.
"""
import pandas as pd
import networkx as nx
from collections import defaultdict
from sqlalchemy import create_engine, text

pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", None)

engine = create_engine(
    "postgresql+psycopg2://eia_user:eia_password@localhost:5432/eia_crude",
    connect_args={"options": "-csearch_path=oil_network,public"}, future=True,
)

# ----------------------------------------------------------------------
# Build the graph from variables (the canonical flow representation).
# ----------------------------------------------------------------------
with engine.connect() as c:
    nodes_df = pd.read_sql(text("""
        SELECT n.node_id, n.node_type, a.attributes->>'kind' AS kind,
               a.attributes->>'starter_status' AS starter_status, l.padd
        FROM nodes n
        JOIN assets a ON a.asset_id = n.asset_id
        JOIN locations l ON l.location_id = a.location_id
        WHERE n.graph_id = 'us_crude_starter'
    """), c)
    edges_df = pd.read_sql(text("""
        SELECT v.node_id AS source, v.related_node_id AS target
        FROM variables v
        JOIN nodes ns ON ns.node_id = v.node_id         AND ns.graph_id = 'us_crude_starter'
        JOIN nodes nt ON nt.node_id = v.related_node_id AND nt.graph_id = 'us_crude_starter'
        WHERE v.variable_type = 'outflow' AND v.commodity = 'crude'
    """), c)

G = nx.DiGraph()
for _, r in nodes_df.iterrows():
    G.add_node(r["node_id"], node_type=r["node_type"], kind=r["kind"],
               padd=r["padd"], starter_status=r["starter_status"])
G.add_edges_from(edges_df.itertuples(index=False, name=None))

# ----------------------------------------------------------------------
# Sources and sinks, partitioned by region.
# ----------------------------------------------------------------------
SOURCES = {
    "permian_tx":              "Permian (TX)",
    "permian_nm":              "Permian (NM)",
    "bakken_nd":               "Bakken (ND)",
    "bakken_mt":               "Bakken (MT)",
    "eagle_ford_tx":           "Eagle Ford (TX)",
    "gulf_of_america":         "Gulf of America offshore",
    "alaska_north_slope":      "Alaska North Slope",
    "california_conventional": "California conventional",
    "oklahoma_conventional":   "Oklahoma conventional",
    "wyoming_conventional":    "Wyoming conventional",
    "colorado_conventional":   "Colorado conventional",
    "canadian_oil_sands":      "Canadian WCSB",
    "padd1_imports_agg":       "PADD1 foreign imports",
    "padd3_imports_agg":       "PADD3 foreign imports",
    "padd5_imports_agg":       "PADD5 foreign imports",
    "clearbrook_entry":        "Clearbrook MN (Mainline US-side)",
}

# Sinks: refineries + export terminals, grouped by region.
SINK_REGIONS = ("PADD1", "PADD2", "PADD3", "PADD4", "PADD5", "EXPORT")
def region_of(node_id):
    nt = G.nodes[node_id]["node_type"]
    if nt == "export_terminal":
        return "EXPORT"
    if nt == "refinery":
        return G.nodes[node_id]["padd"]
    return None

sinks_by_region = defaultdict(list)
for n in G.nodes:
    r = region_of(n)
    if r:
        sinks_by_region[r].append(n)

# ----------------------------------------------------------------------
# Real-world expected reachability (conservative).
# Each cell: True / False / None.  None = "either way fine" (rare flows).
# ----------------------------------------------------------------------
EXPECTED = {
    # source                   PADD1  PADD2  PADD3  PADD4  PADD5  EXPORT
    # Note: Eagle Ford + Gulf offshore reach PADD2 transitively via the
    # bidirectional Houston <-> Seaway <-> Cushing route. In real life this
    # flow is small (most Gulf-area crude stays in PADD3) but topologically
    # permitted, so we mark PADD2 as None ("acceptable either way") rather
    # than False.
    "permian_tx":              [False, True,  True,  False, False, True ],
    "permian_nm":              [False, True,  True,  False, False, True ],
    "bakken_nd":               [False, True,  True,  False, False, True ],  # DAPL/Clearbrook + Cushing-Marketlink
    "bakken_mt":               [False, True,  True,  False, False, True ],
    "eagle_ford_tx":           [False, None,  True,  False, False, True ],  # mostly Corpus+Houston; PADD2 transitive via Seaway
    "gulf_of_america":         [False, None,  True,  False, False, True ],  # offshore -> Gulf hubs + LOOP; PADD2 transitive
    "alaska_north_slope":      [False, False, False, False, True,  False],  # tanker -> West Coast only
    "california_conventional": [False, False, False, False, True,  False],  # islanded
    "oklahoma_conventional":   [False, True,  True,  False, False, True ],  # Cushing -> Midwest + Gulf
    "wyoming_conventional":    [False, True,  True,  True,  False, True ],  # Salt Lake + Sinclair + Pony Express
    "colorado_conventional":   [False, True,  True,  True,  False, True ],  # Suncor + Salt Lake + Pony Express
    "canadian_oil_sands":      [None,  True,  True,  True,  True,  True ],  # 4 corridors reach a lot
    "padd1_imports_agg":       [True,  False, False, False, False, False],
    "padd3_imports_agg":       [False, False, True,  False, False, False],
    "padd5_imports_agg":       [False, False, False, False, True,  False],
    "clearbrook_entry":        [False, True,  True,  False, False, True ],  # Mainline -> Midwest, then Marketlink/Capline -> Gulf
}

# ----------------------------------------------------------------------
# Compute the reachability matrix.
# ----------------------------------------------------------------------
def reachable_in_region(src, region):
    targets = sinks_by_region[region]
    for t in targets:
        try:
            if nx.has_path(G, src, t):
                return True
        except nx.NodeNotFound:
            pass
    return False

print("=" * 110)
print("SOURCE -> SINK REGION REACHABILITY MATRIX")
print("=" * 110)
print(f"  Legend:  yes = expected & reached;  miss = expected but NOT reached;  "
      f"unexp = NOT expected but reached;  ok-skip = correctly skipped\n")

hdr = f"  {'source':32s}  " + "  ".join(f"{r:>7s}" for r in SINK_REGIONS)
print(hdr)
print("  " + "-" * (len(hdr) - 2))

issues = []   # (source, region, kind, message)
for src, label in SOURCES.items():
    if src not in G:
        print(f"  {src:32s}  (NODE NOT IN GRAPH — anomaly)")
        issues.append((src, "*", "missing_node", "source node not present in graph"))
        continue
    cells = []
    for i, region in enumerate(SINK_REGIONS):
        reached = reachable_in_region(src, region)
        expected = EXPECTED[src][i]
        if expected is None:
            cell = "yes" if reached else " - "
        elif reached and expected:
            cell = "yes"
        elif not reached and not expected:
            cell = "ok-skip"
        elif reached and not expected:
            cell = "UNEXP"
            issues.append((src, region, "over_routed",
                           f"{src} reaches a {region} sink but real-world rules say it shouldn't"))
        else:  # expected but not reached
            cell = "miss"
            issues.append((src, region, "under_routed",
                           f"{src} should reach a {region} sink but cannot in the model"))
        cells.append(cell)
    print(f"  {src:32s}  " + "  ".join(f"{c:>7s}" for c in cells))

# ----------------------------------------------------------------------
# Summary of issues
# ----------------------------------------------------------------------
print("\n" + "=" * 110)
print(f"ISSUES FOUND: {len(issues)}")
print("=" * 110)
if not issues:
    print("  Topology matches real-world expectations on every source-sink-region pair.")
else:
    by_kind = defaultdict(list)
    for src, region, kind, msg in issues:
        by_kind[kind].append((src, region, msg))
    for kind, entries in by_kind.items():
        print(f"\n  -- {kind.upper()} ({len(entries)}) --")
        for src, region, msg in entries:
            print(f"     {src:30s} -> {region:6s}   {msg}")

# ----------------------------------------------------------------------
# Per-source detail: list reached refineries + exports with their region.
# ----------------------------------------------------------------------
print("\n" + "=" * 110)
print("PER-SOURCE: REACHABLE REFINERIES + EXPORT TERMINALS")
print("=" * 110)
for src, label in SOURCES.items():
    if src not in G:
        continue
    print(f"\n  {src:32s}  ({label})")
    by_region = defaultdict(list)
    for region in SINK_REGIONS:
        for sink in sinks_by_region[region]:
            try:
                if nx.has_path(G, src, sink):
                    by_region[region].append(sink)
            except nx.NodeNotFound:
                pass
    for region in SINK_REGIONS:
        ts = by_region[region]
        if ts:
            print(f"    {region:7s} ({len(ts):2d}):  {', '.join(sorted(ts))}")

# ----------------------------------------------------------------------
# Reverse view: per-sink, where does crude come from?
# ----------------------------------------------------------------------
print("\n" + "=" * 110)
print("PER-SINK: SOURCES THAT CAN REACH IT (refineries + export terminals)")
print("=" * 110)
all_sinks = sorted(sum(sinks_by_region.values(), []))
for sink in all_sinks:
    sources_reaching = []
    for src in SOURCES:
        if src not in G:
            continue
        try:
            if nx.has_path(G, src, sink):
                sources_reaching.append(src)
        except nx.NodeNotFound:
            pass
    region = region_of(sink)
    status = G.nodes[sink].get("starter_status")
    if not sources_reaching:
        if status == "collapsed":
            note = "<<no source — collapsed sub-terminal under the starter contract; flows route via parent>>"
        else:
            note = "<<NO SOURCE REACHES IT — investigate>>"
        print(f"  {sink:34s} ({region:6s}):  {note}")
    else:
        print(f"  {sink:34s} ({region:6s}):  {', '.join(sources_reaching)}")
