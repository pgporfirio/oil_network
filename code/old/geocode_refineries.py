"""
Patch: backfill `geography.lat` and `geography.lon` on every refinery node added
from the EIA Refinery Capacity Report (refcap25.xlsx) — the report has SITE +
STATE_NAME but no coordinates, so we fill them from a manual lookup table of
city-center coordinates (one per (SITE, STATE) pair).

Coordinates are city-center / refinery-area approximate (typically within
~5-10 km of the actual refinery footprint) — fine for a map view.

Idempotent.
"""
import json
from datetime import datetime
from pathlib import Path

GRAPH = Path(__file__).parent / "asset_graph" / "asset_graph.json"
BACKUP = GRAPH.with_name(f"asset_graph.backup_pre_geocode_{datetime.now():%Y%m%d_%H%M%S}.json")


# (SITE, STATE_NAME) -> (lat, lon).  All 84 unique combinations from the new
# refineries added in add_refinery_capacity_report.py.
CITY_COORDS = {
    ("ANACORTES", "Washington"):                (48.5163, -122.6126),
    ("ARDMORE", "Oklahoma"):                    (34.1740, -97.1437),
    ("ARTESIA", "New Mexico"):                  (32.8420, -104.4039),
    ("ATMORE", "Alabama"):                      (31.0241, -87.4936),
    ("BAKERSFIELD", "California"):              (35.3733, -119.0187),
    ("BATON ROUGE", "Louisiana"):               (30.4515, -91.1871),
    ("BEAUMONT", "Texas"):                      (30.0860, -94.1018),
    ("BENICIA", "California"):                  (38.0494, -122.1581),
    ("BIG SPRING", "Texas"):                    (32.2504, -101.4787),
    ("BILLINGS", "Montana"):                    (45.7833, -108.5007),
    ("BORGER", "Texas"):                        (35.6678, -101.3974),
    ("BRADFORD", "Pennsylvania"):               (41.9609, -78.6403),
    ("CANTON", "Ohio"):                         (40.7989, -81.3784),
    ("CHALMETTE", "Louisiana"):                 (29.9427, -89.9637),
    ("CHANNELVIEW", "Texas"):                   (29.7780, -95.1141),
    ("COFFEYVILLE", "Kansas"):                  (37.0376, -95.6164),
    ("CORPUS CHRISTI", "Texas"):                (27.8006, -97.3964),
    ("CORPUS CHRISTI EAST", "Texas"):           (27.8200, -97.3800),
    ("CORPUS CHRISTI WEST", "Texas"):           (27.7800, -97.4500),
    ("COTTON VALLEY", "Louisiana"):             (32.8157, -93.4221),
    ("DEER PARK", "Texas"):                     (29.7053, -95.1230),
    ("DETROIT", "Michigan"):                    (42.3314, -83.0458),
    ("EL DORADO", "Arkansas"):                  (33.2076, -92.6663),
    ("EL DORADO", "Kansas"):                    (37.8170, -96.8625),
    ("EL PASO", "Texas"):                       (31.7619, -106.4850),
    ("EL SEGUNDO", "California"):               (33.9192, -118.4165),
    ("ELY", "Nevada"):                          (39.2474, -114.8916),
    ("EVANSTON", "Wyoming"):                    (41.2683, -110.9632),
    ("EVANSVILLE", "Wyoming"):                  (42.8639, -106.2703),
    ("GALENA PARK", "Texas"):                   (29.7375, -95.2305),
    ("GALVESTON", "Texas"):                     (29.3013, -94.7977),
    ("GALVESTON BAY", "Texas"):                 (29.3838, -94.9027),
    ("GREAT FALLS", "Montana"):                 (47.5052, -111.3008),
    ("HOUSTON", "Texas"):                       (29.7604, -95.3698),
    ("KAPOLEI", "Hawaii"):                      (21.3361, -158.0577),
    ("KENAI", "Alaska"):                        (60.5544, -151.2583),
    ("KERN", "California"):                     (35.3000, -118.7000),
    ("KROTZ SPRINGS", "Louisiana"):             (30.5354, -91.7546),
    ("LAUREL", "Montana"):                      (45.6722, -108.7707),
    ("LEMONT", "Illinois"):                     (41.6739, -88.0017),
    ("LIMA", "Ohio"):                           (40.7426, -84.1052),
    ("MANDAN", "North Dakota"):                 (46.8267, -100.8896),
    ("MARTINEZ", "California"):                 (38.0194, -122.1341),
    ("MCPHERSON", "Kansas"):                    (38.3708, -97.6642),
    ("MEMPHIS", "Tennessee"):                   (35.1495, -90.0490),
    ("MERAUX", "Louisiana"):                    (29.9402, -89.9395),
    ("MOUNT VERNON", "Indiana"):                (37.9331, -87.8956),
    ("NEWCASTLE", "Wyoming"):                   (43.8541, -104.2055),
    ("NEWELL", "West Virginia"):                (40.6173, -80.6042),
    ("NIXON", "Texas"):                         (29.2659, -97.7625),
    ("NORCO", "Louisiana"):                     (30.0001, -90.4209),
    ("NORTH POLE", "Alaska"):                   (64.7511, -147.3494),
    ("PASADENA", "Texas"):                      (29.6911, -95.2091),
    ("PASCAGOULA", "Mississippi"):              (30.3658, -88.5561),
    ("PAULSBORO", "New Jersey"):                (39.8307, -75.2407),
    ("PORT ALLEN", "Louisiana"):                (30.4519, -91.2104),
    ("PRINCETON", "Louisiana"):                 (32.5723, -93.5021),
    ("PRUDHOE BAY", "Alaska"):                  (70.2553, -148.3373),
    ("ROBINSON", "Illinois"):                   (39.0053, -87.7395),
    ("SAINT PAUL", "Minnesota"):                (44.8118, -93.0117),  # Pine Bend / Rosemount area, FHR refinery
    ("SAN ANTONIO", "Texas"):                   (29.4241, -98.4936),
    ("SANDERSVILLE", "Mississippi"):            (31.7878, -89.0392),
    ("SARALAND", "Alabama"):                    (30.8208, -88.0728),
    ("SHREVEPORT", "Louisiana"):                (32.5252, -93.7502),
    ("SMACKOVER", "Arkansas"):                  (33.3640, -92.7321),
    ("SOUTH GATE", "California"):               (33.9544, -118.2120),
    ("SUNRAY", "Texas"):                        (36.0125, -101.8255),
    ("SUPERIOR", "Wisconsin"):                  (46.7208, -92.1041),
    ("SWEENY", "Texas"):                        (29.0386, -95.7008),
    ("TACOMA", "Washington"):                   (47.2529, -122.4443),
    ("THREE RIVERS", "Texas"):                  (28.4658, -98.1789),
    ("TORRANCE", "California"):                 (33.8358, -118.3406),
    ("TRAINER", "Pennsylvania"):                (39.8231, -75.4119),
    ("TULSA EAST", "Oklahoma"):                 (36.1539, -95.9100),
    ("TULSA WEST", "Oklahoma"):                 (36.1539, -96.0500),
    ("TUSCALOOSA", "Alabama"):                  (33.2098, -87.5692),
    ("TYLER", "Texas"):                         (32.3513, -95.3011),
    ("VALDEZ", "Alaska"):                       (61.1308, -146.3483),
    ("VICKSBURG", "Mississippi"):               (32.3526, -90.8779),
    ("WARREN", "Pennsylvania"):                 (41.8439, -79.1442),
    ("WESTLAKE", "Louisiana"):                  (30.2474, -93.2477),
    ("WILMINGTON ASPHALT PLANT", "California"): (33.7869, -118.2654),
    ("WILMINGTON REFINERY", "California"):      (33.7869, -118.2654),
    ("WYNNEWOOD", "Oklahoma"):                  (34.6418, -97.1581),
}


def main():
    raw = GRAPH.read_text(encoding="utf-8")
    BACKUP.write_text(raw, encoding="utf-8")
    print(f"backup -> {BACKUP.name}")

    g = json.loads(raw)
    n_filled = 0
    n_skipped = 0
    n_already = 0
    missing_cities = set()

    for node in g["nodes"]:
        if node.get("node_subtype") != "refinery":
            continue
        geo = node.get("geography") or {}
        if geo.get("lat") is not None:
            n_already += 1
            continue
        site = (node.get("configuration") or {}).get("site")
        state = geo.get("state")
        if site is None or state is None:
            n_skipped += 1
            continue
        coords = CITY_COORDS.get((site, state))
        if coords is None:
            missing_cities.add((site, state))
            n_skipped += 1
            continue
        lat, lon = coords
        node["geography"]["lat"] = lat
        node["geography"]["lon"] = lon
        n_filled += 1

    print(f"  refineries with coords already (no change): {n_already}")
    print(f"  refineries filled in this run:               {n_filled}")
    print(f"  refineries skipped (no city/state or unmapped): {n_skipped}")
    if missing_cities:
        print(f"  unmapped city-state pairs ({len(missing_cities)}):")
        for c in sorted(missing_cities):
            print(f"    {c}")
    print()
    GRAPH.write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {GRAPH.name}")


if __name__ == "__main__":
    main()
