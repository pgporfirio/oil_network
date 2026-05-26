"""Generate oil_network_map_resolver.html — geographic map of physical assets
with per-node counts of *resolved* values (observed + derived) from
oil_network.scenario_resolved_values instead of just TS-bound assignment counts.

For nodes whose variables are TS-bound the count matches the original map.
For nodes carrying derived/computed values (residuals like padd2_other, basin
states like permian_tx, Canadian-oil-sands as sum_over_outflows, etc.) the
resolver count is higher than the original's `n_ts` — that's the only
intentional visible difference.

Run order:
  1) python resolve_scenario.py
  2) python make_map_resolver_ui.py

Output: oil_network_map_resolver.html.
"""
from __future__ import annotations
from paths import HTML_DIR

import json
from collections import defaultdict
from pathlib import Path

import psycopg2

import make_map as base
from variable_data import fetch_node_variables, fetch_series

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_map_resolver.html"
SCENARIO = "starter_us_crude_2015_2025"

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


def fetch_from_resolver():
    """Same return shape as make_map.fetch(): nodes, counts, edges. The
    `counts` dict per node now includes `n_resolved` (number of distinct
    variables on this node that the resolver produced a non-NULL value for
    at any date in the scenario)."""
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.name, a.node_subtype,
                   l.lat, l.lon, l.padd, l.state
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.kind = 'physical'
            ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        nodes = [dict(zip(cols, r)) for r in cur.fetchall()]

        # Per-node counts: total variables, observed (TS-bound), and any
        # non-NULL resolved value (catches the derived/closure cases too).
        cur.execute("""
            SELECT v.node_id,
                   COUNT(DISTINCT v.variable_id) AS n_vars,
                   COUNT(DISTINCT v.variable_id) FILTER (
                     WHERE EXISTS (
                       SELECT 1 FROM oil_network.scenario_resolved_values srv
                       WHERE srv.scenario_id  = %s
                         AND srv.variable_id  = v.variable_id
                         AND srv.source       = 'observed'
                         AND srv.value IS NOT NULL
                     )
                   ) AS n_ts,
                   COUNT(DISTINCT v.variable_id) FILTER (
                     WHERE EXISTS (
                       SELECT 1 FROM oil_network.scenario_resolved_values srv
                       WHERE srv.scenario_id  = %s
                         AND srv.variable_id  = v.variable_id
                         AND srv.source IN ('observed', 'derived')
                         AND srv.value IS NOT NULL
                     )
                   ) AS n_resolved
            FROM oil_network.variables v
            GROUP BY v.node_id
        """, (SCENARIO, SCENARIO))
        counts = {r[0]: {"n_vars": r[1], "n_ts": r[2], "n_resolved": r[3]}
                  for r in cur.fetchall()}

        cur.execute("""
            SELECT e.source, e.target, e.commodity, e.via_variable
            FROM oil_network.v_flow_edges e
            JOIN oil_network.assets sa ON sa.asset_id = e.source
            JOIN oil_network.assets ta ON ta.asset_id = e.target
            WHERE sa.kind = 'physical' AND ta.kind = 'physical'
        """)
        edges = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    # Per-node variable details + per-variable time series (delegated to the
    # shared variable_data module so make_partition_map.py can reuse them).
    node_vars = fetch_node_variables(SCENARIO)
    dates, series = fetch_series(SCENARIO)

    # Reuse the coordinate-inference logic from the original (lifted from make_map.fetch())
    coords = {n["asset_id"]: (n["lat"], n["lon"]) for n in nodes if n["lat"] is not None}
    neighbours = defaultdict(set)
    for e in edges:
        neighbours[e["source"]].add(e["target"])
        neighbours[e["target"]].add(e["source"])
    for _pass in range(3):
        changed = False
        for n in nodes:
            nid = n["asset_id"]
            if nid in coords:
                continue
            nb_coords = [coords[nb] for nb in neighbours.get(nid, []) if nb in coords]
            if nb_coords:
                lat = sum(c[0] for c in nb_coords) / len(nb_coords)
                lon = sum(c[1] for c in nb_coords) / len(nb_coords)
                coords[nid] = (lat, lon)
                n["lat"], n["lon"] = lat, lon
                n["inferred_coords"] = True
                changed = True
        if not changed:
            break

    return nodes, counts, edges, node_vars, dates, series


def main():
    print(f"Reading scenario_resolved_values for scenario {SCENARIO!r}...")
    nodes, counts, edges, node_vars, dates, series = fetch_from_resolver()
    print(f"  physical nodes: {len(nodes)}, edges: {len(edges)}")
    n_observed = sum(1 for c in counts.values() if c["n_ts"] > 0)
    n_resolved = sum(1 for c in counts.values() if c["n_resolved"] > 0)
    print(f"  nodes with observed variables: {n_observed}")
    print(f"  nodes with any resolved value: {n_resolved}  (incl. derived)")
    n_vars_total = sum(len(v) for v in node_vars.values())
    print(f"  variable rows embedded in HTML: {n_vars_total} across {len(node_vars)} nodes")
    print(f"  time-series embedded: {len(series)} variables x {len(dates)} dates")

    payload = base.build_payload(nodes, counts, edges, node_vars=node_vars)
    payload["dates"] = dates
    payload["series"] = series

    html = base.HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    # The base template carries four placeholders. Earlier versions of this
    # file only substituted DATA + the three count placeholders, leaving
    # __SUBTYPE_COLOR__ / __SUBTYPE_LABEL__ as literal text inside the
    # <script> block — a JS parse error that killed Plotly.newPlot before
    # any trace was rendered. The map appeared empty as a result. Both
    # substitutions are required.
    html = html.replace("__SUBTYPE_COLOR__", json.dumps(base.SUBTYPE_COLOR))
    html = html.replace("__SUBTYPE_LABEL__", json.dumps(base.SUBTYPE_LABEL))
    html = html.replace("__N_NODES__", str(sum(len(v) for v in payload["nodes_by_sub"].values())))
    html = html.replace("__N_EDGES__", str(len(payload["edges"])))
    html = html.replace("__N_SUBTYPES__", str(len(payload["nodes_by_sub"])))
    html = html.replace(
        "oil_network &middot; physical-asset geographic map",
        "oil_network &middot; physical-asset geographic map &middot; <em>resolver</em>",
    )
    from render_utils import latest_run_id, write_html
    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, "map",
                              notes=f"{len(nodes)} physical nodes, {len(edges)} edges")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)  "
          f"[artefact_id={artefact_id}, run_id={run_id}]")


if __name__ == "__main__":
    main()
