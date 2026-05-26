"""Generate oil_network_hierarchy_resolver.html — same as make_hierarchy_explorer
but the per-timeseries data points come from `oil_network.scenario_resolved_values`
(restricted to source='observed') instead of `oil_network.timeseries_data` directly.

For TS-bound variables the two sources are identical by construction — this file
exists primarily to confirm that the resolver round-trips observed values
faithfully, so you can compare the two HTML outputs side-by-side.

Run order:
  1) python resolve_scenario.py
  2) python make_hierarchy_resolver_ui.py

Output: oil_network_hierarchy_resolver.html.
"""
from __future__ import annotations
from paths import HTML_DIR

import json
from collections import defaultdict
from pathlib import Path

import psycopg2

import make_hierarchy_explorer as base
from render_utils import latest_run_id, write_html

ROOT = Path(__file__).parent
HTML_OUT = HTML_DIR / "oil_network_hierarchy_resolver.html"
SCENARIO = "starter_us_crude_2015_2025"
VIEW_NAME = "hierarchy"

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


def fetch_from_resolver():
    """Returns (assets, variables, ts_data, ts_meta) — same shape as
    make_hierarchy_explorer.fetch() but ts_data is filled from the resolver
    table (source='observed' rows) and keyed by timeseries_id."""
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.name, a.kind, a.node_class, a.node_subtype,
                   l.padd, l.state, l.country
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        assets = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT v.variable_id, v.variable_type, v.commodity, v.node_id, v.related_node_id,
                   va.timeseries_id, va.formula, va.formula_inputs
            FROM oil_network.variables v
            LEFT JOIN oil_network.variable_assignments va ON va.variable_id = v.variable_id
            ORDER BY v.variable_id
        """)
        cols = [c.name for c in cur.description]
        variables = [dict(zip(cols, r)) for r in cur.fetchall()]

        # Pull observed values from the resolver (instead of timeseries_data
        # directly). Group by timeseries_id by joining through assignments.
        # For variables sharing a TS, DISTINCT keeps a single series per TS.
        cur.execute("""
            SELECT timeseries_id, observation_date, value
            FROM (
              SELECT DISTINCT va.timeseries_id, srv.observation_date::text AS observation_date, srv.value
              FROM oil_network.scenario_resolved_values srv
              JOIN oil_network.variable_assignments va USING (variable_id)
              WHERE srv.scenario_id = %s
                AND srv.source = 'observed'
                AND va.timeseries_id IS NOT NULL
                AND srv.value IS NOT NULL
            ) t
            ORDER BY timeseries_id, observation_date
        """, (SCENARIO,))
        ts_data = defaultdict(list)
        for ts_id, obs_date, val in cur.fetchall():
            ts_data[ts_id].append([obs_date, float(val)])

        cur.execute("SELECT timeseries_id, name, unit FROM oil_network.timeseries")
        ts_meta = {row[0]: {"name": row[1], "unit": row[2]} for row in cur.fetchall()}

        # Status comes from the same view as the underlying explorer
        # (Principle 2.4 / 2.8 — derived from variable_assignments).
        cur.execute("""
            SELECT node_id, status, n_ts, n_derived, n_zero, n_latent
            FROM oil_network.v_node_status
            WHERE scenario_id = %s
        """, (SCENARIO,))
        node_status_map = {
            r[0]: {"status": r[1], "n_ts": r[2], "n_derived": r[3],
                   "n_zero": r[4], "n_latent": r[5]}
            for r in cur.fetchall()
        }

    return assets, variables, dict(ts_data), ts_meta, node_status_map


def main():
    print(f"Reading scenario_resolved_values for scenario {SCENARIO!r}...")
    assets, variables, ts_data, ts_meta, node_status_map = fetch_from_resolver()
    print(f"  assets: {len(assets)}, variables: {len(variables)}, ts with data: {len(ts_data)}")

    parent_map = base.build_parent_map(assets, variables)
    payload = base.build_payload(assets, variables, ts_data, ts_meta, parent_map, node_status_map)

    # Multi-parent rendering. The hierarchy view is defined by the variables:
    # a node can be a structural member of multiple parents (e.g. an
    # inter-PADD aggregate belongs to both its sender and receiver PADD,
    # pipe_bakken_xstate belongs to both padd2 and padd4 via its
    # inflow/outflow variables). Use NetworkGraph.partition_parents to get
    # every parent each node has under v_partition_tree, then append the
    # node to the children list of any parent that doesn't already include
    # it (parent_map only assigned one primary).
    from network_graph import NetworkGraph
    g = NetworkGraph(SCENARIO)
    n_multi = 0
    for nid in g.nodes:
        parents = g.partition_parents(nid)
        if len(parents) <= 1:
            continue
        n_multi += 1
        for p in parents:
            sec = payload["nodes"].get(p)
            if sec is None:
                continue
            kids = sec.setdefault("children", [])
            if nid not in kids:
                kids.append(nid)
        for p in parents:
            sec = payload["nodes"].get(p)
            if sec:
                sec["children"].sort()
    if n_multi:
        print(f"  multi-parent nodes promoted to every parent: {n_multi}")
    print(f"  audit: {payload.get('audit', {})}")

    html = base.HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    aud = payload["audit"]
    for k in ("n_nodes", "n_physical", "n_abstract", "n_with_data",
              "n_derived", "n_collapsed", "n_orphans"):
        html = html.replace(f"__{k.upper()}__", str(aud[k]))
    # Banner so it's obvious which version is open
    html = html.replace(
        ">oil_network &middot; hierarchy explorer<",
        ">oil_network &middot; hierarchy explorer &middot; <em>resolver</em><",
    )
    run_id = latest_run_id(SCENARIO)
    artefact_id = write_html(html, HTML_OUT, SCENARIO, run_id, VIEW_NAME,
                              notes=f"{len(assets)} assets, {len(ts_data)} TS series")
    print(f"Wrote {HTML_OUT}  ({HTML_OUT.stat().st_size // 1024} KB)  "
          f"[artefact_id={artefact_id}, run_id={run_id}]")


if __name__ == "__main__":
    main()
