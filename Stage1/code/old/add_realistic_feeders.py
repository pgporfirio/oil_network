"""
Pass 3: add additional realistic feed edges so refineries are not single-fed.

Reflects how major US refineries actually source crude from multiple corridors:
  - Gulf Coast refineries: hub feeds + offshore Gulf direct + LOOP-marine
  - Midwest refineries: Patoka and Cushing both feed via different pipeline systems
  - West Coast: Cherry Point also gets some ANS via tanker

Skipped (single-feeder is realistic for these):
  - ref_p66_bayway, ref_pbf_delaware_city: tanker-fed only (rail not modelled)
  - ref_hf_sinclair_wy, ref_suncor_commerce_city: small local refineries with one regional source
  - ref_marathon_la, ref_chevron_richmond: already 3-fed
  - ref_marathon_garyville: already 2-fed (st_james + loop_terminal)
  - ref_salt_lake_cluster: already 2-fed (colorado + wyoming)

Idempotent.
"""
import json
from collections import Counter
from pathlib import Path

JSON_PATH = Path("asset_graph/asset_graph.json")
G = json.loads(JSON_PATH.read_text(encoding="utf-8"))

def edge(src, tgt, edge_type, **attrs):
    return {
        "id": f"e_{src}__{tgt}__{edge_type}",
        "source": src,
        "target": tgt,
        "edge_type": edge_type,
        "attributes": attrs,
    }

NEW_EDGES = [
    # ----- Gulf Coast: add second feeders -----
    edge("houston_hub", "ref_motiva_port_arthur", "terminal_connector",
         capacity_bpd=300_000, transit_time_days=0.5,
         notes="Houston-area pipelines (Texas Crude, Pasadena Strategic) deliver to Port Arthur in addition to Nederland-area lines."),
    edge("gulf_of_america", "ref_galveston_bay_mpc", "offshore_outflow",
         capacity_bpd=200_000, transit_time_days=2,
         notes="Direct offshore connectors (Texas City Refining lines, shallow-water gathering) land Gulf crude at Galveston Bay."),
    edge("gulf_of_america", "ref_exxon_baytown", "offshore_outflow",
         capacity_bpd=300_000, transit_time_days=2,
         notes="ExxonMobil-operated offshore pipelines (BPP, others) deliver Gulf crude directly to Baytown."),
    edge("loop_terminal", "ref_citgo_lake_charles", "terminal_connector",
         capacity_bpd=150_000, transit_time_days=1,
         notes="LOOP/LOCAP delivers heavy foreign and offshore Gulf crude to Lake Charles."),

    # ----- Midwest: Mainline terminus + Cushing southbound feeds -----
    edge("pipe_enbridge_mainline", "ref_bp_whiting", "pipeline_outflow",
         capacity_bpd=400_000, transit_days=8, direction=None,
         notes="Mainline 6/14/61 systems terminate at Mokena/Whiting; Whiting is the historical southern terminus of the Lakehead system."),
    edge("cushing_hub", "ref_p66_wood_river", "terminal_connector",
         capacity_bpd=200_000, transit_time_days=2,
         notes="Phillips 66's Wood River pipeline (and Mid-Valley reverse-direction lateral) connects Cushing to Wood River refinery."),
    edge("cushing_hub", "ref_marathon_catlettsburg", "terminal_connector",
         capacity_bpd=150_000, transit_time_days=3,
         notes="Mid-Valley pipeline historically delivers Cushing crude to Catlettsburg via Patoka and onward."),
    edge("cushing_hub", "ref_p66_ponca_city", "terminal_connector",
         capacity_bpd=150_000, transit_time_days=0.5,
         notes="Plains/Sunoco WTI delivery pipelines from Cushing to Ponca City refinery (~50 mi north)."),

    # ----- West Coast: ANS tankers also reach Cherry Point -----
    edge("valdez_origin", "ref_bp_cherry_point", "shipping_route",
         vessel_class="Aframax", transit_time_days=8,
         notes="Some Alaska North Slope volumes shipped Valdez -> Pacific Northwest (Cherry Point) by tanker."),
]

existing_edge_ids = {e["id"] for e in G["edges"]}
added = []
for e in NEW_EDGES:
    if e["id"] not in existing_edge_ids:
        G["edges"].append(e)
        added.append(e["id"])

# Refresh meta counts.
G["meta"]["edge_type_counts"] = dict(Counter(e["edge_type"] for e in G["edges"]))

JSON_PATH.write_text(json.dumps(G, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"Added {len(added)} new feed edges:")
for eid in added:
    print(f"  {eid}")
print(f"\nTotal edges now: {len(G['edges'])}")
print("\nEdge type counts:")
for k, v in sorted(G["meta"]["edge_type_counts"].items()):
    print(f"  {k:30s} {v}")
