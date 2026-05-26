"""Patch pipeline capacity timelines into assets.attributes->configuration.

Stores documented capacity changes 2015-2025 (expansions, reversals, deratings)
as a `capacity_history` JSONB array on each pipeline asset, plus a refreshed
`capacity_bpd` for the current value. Per Pedro's policy: keep the information
and the sources in the JSON, but the seeder picks the value it sees fit.

Sources gathered by research subagent on 2026-05-15 from:
  - Wikipedia citations
  - Global Energy Monitor (gem.wiki)
  - Company filings (Enbridge, MPLX, ONEOK, Energy Transfer, TC Energy)
  - S&P Global / Reuters / Oil & Gas Journal
  - RBN Energy / Subcusa industry analysis

Notable updates from the timeline research:
  - pipe_capline: 300 → 200 kbpd (operational, not announced nameplate)
  - pipe_seaway: 950 confirmed; stepped 850→950 in 2016
  - pipe_gray_oak: 900 confirmed; 980 expansion came online 2025 (not yet bound)
  - pipe_taps: 2,100 nameplate stable; operating ~465k

`capacity_history` schema:
  [
    {"effective_from": "YYYY-MM-DD", "capacity_bpd": int, "source": "...", "notes": "..."},
    ...
  ]

`populate_asset_capacities.py` (post-this-patch) reads capacity_history and
emits one row in oil_network.asset_capacities per history entry, so the
LP exporter / audit / resolver see the correct cap for each (asset, date).

Idempotent. After running, re-run populate_asset_capacities.py.
"""
from __future__ import annotations
import json
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


PIPELINE_TIMELINES = {
    "pipe_basin": {
        "current_capacity_bpd": 550_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 450_000,
             "source": "https://www.gem.wiki/Basin_Oil_Pipeline",
             "notes": "Colorado City->Cushing 450k bottleneck pre-2018"},
            {"effective_from": "2018-07-01", "capacity_bpd": 550_000,
             "source": "RBN Energy 'Tequila Sunrise'",
             "notes": "Sunrise expansion + Basin pump upgrades"},
        ],
        "direction_notes": "Permian (Wink/Midland) -> Cushing; northbound, stable",
    },
    "pipe_bridgetex": {
        "current_capacity_bpd": 440_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 300_000,
             "source": "https://www.gem.wiki/BridgeTex_Oil_Pipeline",
             "notes": "Original nameplate post-2014 startup"},
            {"effective_from": "2017-07-01", "capacity_bpd": 400_000,
             "source": "PRNewswire Jan 2017 BridgeTex expansion announcement",
             "notes": "Pump-station expansion in service Q2 2017"},
            {"effective_from": "2019-04-01", "capacity_bpd": 440_000,
             "source": "Subcusa: BridgeTex supplemental open season",
             "notes": "+40k expansion in service early 2019; ONEOK took over 2024"},
        ],
        "direction_notes": "Colorado City/Midland -> Houston/Texas City",
    },
    "pipe_cactus_ii": {
        "current_capacity_bpd": 670_000,
        "history": [
            {"effective_from": "2019-08-12", "capacity_bpd": 585_000,
             "source": "https://www.nsenergybusiness.com/projects/cactus-ii-pipeline/",
             "notes": "Partial in-service Aug 2019 at design 585k"},
            {"effective_from": "2020-04-01", "capacity_bpd": 670_000,
             "source": "https://www.gem.wiki/Cactus_II_Oil_Pipeline",
             "notes": "Full nameplate Q2 2020 after additional pumping"},
        ],
        "direction_notes": "Permian -> Corpus Christi/Ingleside; southbound",
    },
    "pipe_capline": {
        "current_capacity_bpd": 200_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 1_200_000,
             "source": "RBN Energy: Southbound - What's Ahead for Capline",
             "notes": "Nameplate northbound (St James -> Patoka); operational far lower"},
            {"effective_from": "2019-01-01", "capacity_bpd": 0,
             "source": "RBN Energy: Capline reversal commit",
             "notes": "Idled pending reversal works"},
            {"effective_from": "2021-12-18", "capacity_bpd": 200_000,
             "source": "MPLX press release Dec 18 2021",
             "notes": "Interim southbound service light crude"},
            {"effective_from": "2022-01-01", "capacity_bpd": 200_000,
             "source": "S&P Global commodity-insights Jan 7 2022",
             "notes": "Full southbound; nameplate 300k achievable with pump reactivation"},
        ],
        "direction_notes": "REVERSED Jan 2022; pre-2022 northbound, now southbound Patoka->St James. Operational 200k.",
    },
    "pipe_dapl_etco": {
        "current_capacity_bpd": 750_000,
        "history": [
            {"effective_from": "2017-06-01", "capacity_bpd": 470_000,
             "source": "https://en.wikipedia.org/wiki/Dakota_Access_Pipeline",
             "notes": "Commercial in-service June 2017 at 470k"},
            {"effective_from": "2018-08-01", "capacity_bpd": 570_000,
             "source": "https://www.gem.wiki/Dakota_Access_Oil_Pipeline_(DAPL)",
             "notes": "DRA + pump optimization"},
            {"effective_from": "2021-08-01", "capacity_bpd": 750_000,
             "source": "Pipeline & Gas Journal Aug 2021",
             "notes": "Bakken Pipeline Optimization Phase 1 completed"},
        ],
        "direction_notes": "Bakken -> Patoka -> Nederland; southbound",
    },
    "pipe_enbridge_mainline": {
        "current_capacity_bpd": 600_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 600_000,
             "source": "Enbridge fact sheet",
             "notes": "Flanagan South leg 600k; treated as the US portion in this graph"},
        ],
        "direction_notes": "US southern leg of Enbridge Mainline (Flanagan South: Pontiac IL -> Cushing OK)",
    },
    "pipe_epic_crude": {
        "current_capacity_bpd": 600_000,
        "history": [
            {"effective_from": "2019-08-01", "capacity_bpd": 400_000,
             "source": "https://www.gem.wiki/EPIC_Oil_Pipeline",
             "notes": "Interim crude service Aug 2019 on what was originally an NGL line"},
            {"effective_from": "2020-04-01", "capacity_bpd": 600_000,
             "source": "https://epicmid.com/projects/crude-pipeline/",
             "notes": "Full crude service April 2020 at 600k; expandable to 1MM"},
        ],
        "direction_notes": "Permian -> Corpus Christi; southbound, stable since 2020",
    },
    "pipe_gray_oak": {
        "current_capacity_bpd": 900_000,
        "history": [
            {"effective_from": "2019-11-01", "capacity_bpd": 900_000,
             "source": "Natural Gas Intelligence Nov 2019",
             "notes": "Initial service; expansion to 980k completed 2025 (not yet captured)"},
        ],
        "direction_notes": "Permian/Eagle Ford -> Corpus Christi/Sweeny; southbound",
    },
    "pipe_harvest_eagle_ford": {
        "current_capacity_bpd": 380_000,
        "history": [],
        "direction_notes": "Eagle Ford gathering -> Three Rivers/Corpus Christi; stable pre-2015. Operator: Harvest Midstream/Hilcorp (not ExxonMobil JV)",
    },
    "pipe_kmcc": {
        "current_capacity_bpd": 350_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 300_000,
             "source": "https://www.gem.wiki/Kinder_Morgan_Crude_Condensate_(KMCC)_Pipeline",
             "notes": "Original 300k from 2012 startup"},
            {"effective_from": "2019-11-01", "capacity_bpd": 350_000,
             "source": "Same source",
             "notes": "Expansion tied to Gray Oak interconnect"},
        ],
        "direction_notes": "Eagle Ford -> Houston ship channel",
    },
    "pipe_locap": {
        "current_capacity_bpd": 1_700_000,
        "history": [],
        "direction_notes": "LOOP Clovelly <-> St James; stable pre-2015. Nominal 1.7MM, expandable to 2.4MM",
    },
    "pipe_longhorn": {
        "current_capacity_bpd": 275_000,
        "history": [],
        "direction_notes": "Crane TX -> Houston (eastbound, reversed 2013). Stable 2015-2025",
    },
    "pipe_loop_connector": {
        "current_capacity_bpd": 1_200_000,
        "history": [
            {"effective_from": "2018-02-01", "capacity_bpd": 1_200_000,
             "source": "https://www.gem.wiki/Louisiana_Offshore_Oil_Port_(LOOP)_Pipeline",
             "notes": "Bidirectional service began Feb 2018"},
        ],
        "direction_notes": "Bidirectional since Feb 2018; previously import-only northbound",
    },
    "pipe_marketlink": {
        "current_capacity_bpd": 750_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 700_000,
             "source": "https://www.gem.wiki/Keystone_Oil_Pipeline",
             "notes": "Original Cushing-Gulf MarketLink nameplate post-2014 commissioning"},
            {"effective_from": "2017-01-01", "capacity_bpd": 750_000,
             "source": "TC Energy MarketLink shipper info",
             "notes": "Updated nameplate after pump enhancements"},
        ],
        "direction_notes": "Cushing -> Nederland; southbound",
    },
    "pipe_midland_to_echo": {
        "current_capacity_bpd": 620_000,
        "history": [
            {"effective_from": "2017-06-01", "capacity_bpd": 300_000,
             "source": "https://www.gem.wiki/Midland-to-ECHO_Pipeline_System",
             "notes": "Initial service Q2 2017"},
            {"effective_from": "2018-05-01", "capacity_bpd": 575_000,
             "source": "Same source",
             "notes": "Expansion completed May 2018"},
            {"effective_from": "2019-03-01", "capacity_bpd": 620_000,
             "source": "Same source",
             "notes": "+45k expansion March 2019"},
        ],
        "direction_notes": "Midland -> ECHO Houston; eastbound, stable since 2019",
    },
    "pipe_seaway": {
        "current_capacity_bpd": 950_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 850_000,
             "source": "https://en.wikipedia.org/wiki/Seaway_Pipeline",
             "notes": "Twin completed mid-2014; combined system 850k"},
            {"effective_from": "2016-01-01", "capacity_bpd": 950_000,
             "source": "https://www.gem.wiki/Seaway_Oil_Pipeline_System",
             "notes": "Pump optimization to 950k combined"},
        ],
        "direction_notes": "Reversed 2012; southbound Cushing -> Freeport/Houston. Stable at 950k since ~2016",
    },
    "pipe_spearhead": {
        "current_capacity_bpd": 193_000,
        "history": [],
        "direction_notes": "Flanagan IL -> Cushing OK; southbound. Stable since pre-2015. Long-term contracts expiring 2026",
    },
    "pipe_taps": {
        "current_capacity_bpd": 2_100_000,
        "history": [
            {"effective_from": "2015-01-01", "capacity_bpd": 2_100_000,
             "source": "https://alyeska-pipe.com/historic-throughput/",
             "notes": "Nameplate unchanged; actual throughput ~465-500k (severe under-utilisation)"},
        ],
        "direction_notes": "Prudhoe Bay -> Valdez; southbound. Nameplate stable. Use a variable_constraints overlay for operating limit",
    },
    "pipe_wink_to_webster": {
        "current_capacity_bpd": 1_500_000,
        "history": [
            {"effective_from": "2020-10-01", "capacity_bpd": 1_000_000,
             "source": "Oil & Gas Journal Oct 2020",
             "notes": "Initial commercial service Q4 2020 at 1MM"},
            {"effective_from": "2021-07-01", "capacity_bpd": 1_500_000,
             "source": "oilgasleads.com Permian superhighway",
             "notes": "Full nameplate 1.5MM achieved 2021"},
        ],
        "direction_notes": "Wink/Midland -> Webster/Baytown; eastbound",
    },
}

SOURCE = "research_pass_2026_05_15_timeline"


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("=== Patching pipeline timelines into assets.attributes->configuration ===\n")
        n = 0
        for asset_id, payload in PIPELINE_TIMELINES.items():
            patch = {
                "capacity_bpd": payload["current_capacity_bpd"],
                "capacity_bpd_source": SOURCE,
                "capacity_history": payload["history"],
                "direction_notes": payload["direction_notes"],
            }
            cur.execute("""
                UPDATE oil_network.assets
                SET attributes = jsonb_set(
                    attributes, '{configuration}',
                    COALESCE(attributes->'configuration', '{}'::jsonb) || %s::jsonb
                )
                WHERE asset_id = %s
                RETURNING asset_id
            """, (json.dumps(patch), asset_id))
            if cur.fetchone():
                n_hist = len(payload["history"])
                print(f"  {asset_id:28s} current={payload['current_capacity_bpd']:>9,}  history_entries={n_hist}")
                n += 1
            else:
                print(f"  ! {asset_id:28s} NOT FOUND")
        conn.commit()
        print(f"\nDone. {n} pipeline timelines patched.")


if __name__ == "__main__":
    main()
