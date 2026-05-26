"""Node-by-node sanity check of the oil_network asset graph.

For each physical node, pulls in/out edges from Postgres and cross-references
against rough real-world flows. Prints findings + flags routing gaps.
"""
import pandas as pd
from sqlalchemy import create_engine, text

pd.set_option("display.max_colwidth", None)
pd.set_option("display.width", 200)

engine = create_engine(
    "postgresql+psycopg2://eia_user:eia_password@localhost:5432/eia_crude",
    connect_args={"options": "-csearch_path=oil_network,public"}, future=True,
)

with engine.connect() as c:
    df = pd.read_sql(text("""
        SELECT n.node_id,
               n.node_type,
               a.attributes->>'kind' AS kind,
               a.attributes->'configuration' AS configuration,
               (SELECT array_agg(related_node_id ORDER BY related_node_id)
                  FROM variables WHERE variable_type='outflow' AND node_id=n.node_id
                    AND commodity='crude') AS out_to,
               (SELECT array_agg(node_id ORDER BY node_id)
                  FROM variables WHERE variable_type='outflow' AND related_node_id=n.node_id
                    AND commodity='crude') AS in_from
        FROM nodes n JOIN assets a ON a.asset_id=n.asset_id
        WHERE n.graph_id='us_crude_starter' AND a.attributes->>'kind'='physical'
        ORDER BY n.node_type, n.node_id
    """), c)

PROD_KBD = {
    "permian_tx": 4600, "permian_nm": 1100, "bakken_nd": 1100, "bakken_mt": 60,
    "eagle_ford_tx": 1200, "gulf_of_america": 1800, "alaska_north_slope": 420,
    "california_conventional": 290, "oklahoma_conventional": 400,
    "wyoming_conventional": 270, "colorado_conventional": 450,
}
EXPECTED_DESTS = {
    "permian_tx":              "Gulf Coast (Houston, Corpus, Nederland) via Gray Oak, Cactus II, Midland-ECHO, BridgeTex, Longhorn; Cushing via Basin",
    "permian_nm":              "Gulf via EPIC + Cactus II (Wink/Midland origins); some via Basin to Cushing",
    "bakken_nd":               "Cushing/Patoka via DAPL; Clearbrook via Enbridge ND; St. James via Capline-reversed; rail to PADDs 1+5",
    "bakken_mt":               "Same as Bakken-ND via shared gathering; some westbound to Billings refining (not modelled)",
    "eagle_ford_tx":           "Corpus Christi exports + Houston refining (Harvest, EPIC, KMCC pipelines)",
    "gulf_of_america":         "Houston (Mars/Olympus/Magellan), LOOP/Clovelly (BPP/Endymion), Garyville/St_James",
    "alaska_north_slope":      "TAPS to Valdez to tanker to West Coast refineries (LA, Bay Area, Cherry Point)",
    "california_conventional": "Islanded: refines locally only (LA basin, Bay Area). No interstate pipeline.",
    "oklahoma_conventional":   "Cushing via Plains/Sunoco gathering; in-state Ponca City refining (not modelled)",
    "wyoming_conventional":    "Sinclair WY (~85 kbd local), Salt Lake refineries, surplus to Cushing via Pony Express/Platte",
    "colorado_conventional":   "Suncor Commerce City (98 kbd, not modelled); surplus to Cushing via White Cliffs/Pony Express; some Salt Lake",
}

CAP_KBD_REFINERY = {
    "ref_motiva_port_arthur": 626, "ref_galveston_bay_mpc": 593,
    "ref_exxon_baytown": 561, "ref_marathon_garyville": 597,
    "ref_citgo_lake_charles": 419, "ref_bp_whiting": 435,
    "ref_p66_wood_river": 356, "ref_exxon_joliet": 271,
    "ref_marathon_catlettsburg": 291, "ref_pbf_toledo": 170,
    "ref_marathon_la": 363, "ref_chevron_richmond": 245,
    "ref_bp_cherry_point": 251, "ref_p66_bayway": 258,
    "ref_pbf_delaware_city": 190, "ref_salt_lake_cluster": 180,
    "ref_hf_sinclair_wy": 80,
}
EXPECTED_REFINERY_FEED = {
    "ref_motiva_port_arthur":    "Nederland local; Cushing via Seaway/MarketLink; foreign tankers (PADD3 imports not modelled)",
    "ref_galveston_bay_mpc":     "Houston hub; some offshore Gulf direct; foreign tankers",
    "ref_exxon_baytown":         "Houston hub (Magtex etc.); some Permian via Midland-ECHO and Houston",
    "ref_marathon_garyville":    "St. James via Capline-reversed; offshore Gulf via LOCAP/LOOP; foreign tankers",
    "ref_citgo_lake_charles":    "Nederland; foreign Venezuelan crude historically (heavy slate)",
    "ref_bp_whiting":            "Patoka via Enbridge mainline (Mokena terminus); Bakken via Clearbrook -> mainline; some Permian",
    "ref_p66_wood_river":        "Patoka (Wood River pipeline + Mid-Valley); some Cushing",
    "ref_exxon_joliet":          "Patoka via Mokena spur from mainline",
    "ref_marathon_catlettsburg": "Patoka via Mid-Valley; some Capline historic",
    "ref_pbf_toledo":            "Patoka; Canadian Sarnia connector (not modelled)",
    "ref_marathon_la":           "Valdez tankers; CA conventional; foreign imports",
    "ref_chevron_richmond":      "Same",
    "ref_bp_cherry_point":       "Trans Mountain (not modelled); foreign tankers; some BNSF Bakken rail",
    "ref_p66_bayway":            "Foreign tanker imports primarily; some Bakken rail",
    "ref_pbf_delaware_city":     "Foreign tanker imports primarily; some Bakken rail",
    "ref_salt_lake_cluster":    "Local Uinta (not modelled); Wyoming sweet; Colorado",
    "ref_hf_sinclair_wy":        "Local Wyoming production",
}

def banner(title):
    print(f"\n{'='*100}\n{title}\n{'='*100}")

# Production nodes
banner("PRODUCTION NODES - does the routing make sense?")
for _, r in df[df.node_type.isin(("state_sub_basin", "state_conventional", "offshore_region"))].iterrows():
    nid = r.node_id
    out = list(r.out_to) if r.out_to is not None else []
    prod = PROD_KBD.get(nid, "?")
    print(f"\n{nid:30s} ({r.node_type})  prod ~{prod} kbd")
    print(f"  current out -> {out}")
    print(f"  expected:    {EXPECTED_DESTS.get(nid, '?')}")

# Refineries
banner("REFINERIES - does the feed make sense?")
for _, r in df[df.node_type == "refinery"].iterrows():
    nid = r.node_id
    cap = CAP_KBD_REFINERY.get(nid, "?")
    inn = list(r.in_from) if r.in_from is not None else []
    print(f"\n{nid:30s}  capacity ~{cap} kbd")
    print(f"  current in <- {inn}")
    print(f"  expected:    {EXPECTED_REFINERY_FEED.get(nid, '?')}")

# Storage hubs
banner("STORAGE HUBS - in/out summary")
for _, r in df[df.node_type == "storage_terminal"].iterrows():
    inn = list(r.in_from) if r.in_from is not None else []
    out = list(r.out_to) if r.out_to is not None else []
    cfg = r.configuration or {}
    cap = cfg.get("storage_capacity_mbbl")
    print(f"\n{r.node_id:30s}  storage ~{cap} mbbl")
    print(f"  in  <- ({len(inn):2d}): {inn}")
    print(f"  out -> ({len(out):2d}): {out}")

# Origin terminals
banner("ORIGIN TERMINALS - in/out summary")
for _, r in df[df.node_type == "origin_terminal"].iterrows():
    inn = list(r.in_from) if r.in_from is not None else []
    out = list(r.out_to) if r.out_to is not None else []
    print(f"\n{r.node_id:30s}")
    print(f"  in  <- ({len(inn):2d}): {inn}")
    print(f"  out -> ({len(out):2d}): {out}")

# Pipelines
banner("PIPELINES - endpoints sanity")
for _, r in df[df.node_type == "pipeline"].iterrows():
    inn = list(r.in_from) if r.in_from is not None else []
    out = list(r.out_to) if r.out_to is not None else []
    cfg = r.configuration or {}
    cap = cfg.get("capacity_bpd")
    print(f"  {r.node_id:28s} cap={str(cap):>10s} | in <- {inn}  | out -> {out}")

# Gathering nodes
banner("GATHERING NODES - in/out summary")
for _, r in df[df.node_type == "gathering"].iterrows():
    inn = list(r.in_from) if r.in_from is not None else []
    out = list(r.out_to) if r.out_to is not None else []
    print(f"  {r.node_id:30s} | in <- {inn}  | out -> {out}")

# Export terminals
banner("EXPORT TERMINALS - in summary")
for _, r in df[df.node_type == "export_terminal"].iterrows():
    inn = list(r.in_from) if r.in_from is not None else []
    out = list(r.out_to) if r.out_to is not None else []
    cfg = r.configuration or {}
    cap = cfg.get("loading_capacity_bpd") or cfg.get("capacity_bpd")
    print(f"  {r.node_id:32s} cap={str(cap):>10s} | in <- {inn}  | out -> {out}")

# Import terminals
banner("IMPORT TERMINALS")
for _, r in df[df.node_type == "import_terminal"].iterrows():
    inn = list(r.in_from) if r.in_from is not None else []
    out = list(r.out_to) if r.out_to is not None else []
    print(f"  {r.node_id:32s} | in <- {inn}  | out -> {out}")

# SPR sites
banner("SPR SITES")
for _, r in df[df.node_type == "spr_site"].iterrows():
    inn = list(r.in_from) if r.in_from is not None else []
    out = list(r.out_to) if r.out_to is not None else []
    cfg = r.configuration or {}
    cap = cfg.get("storage_capacity_mbbl")
    print(f"  {r.node_id:30s} cap={cap:>5} mbbl | fill <- {inn}  | release -> {out}")
