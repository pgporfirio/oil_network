"""Shared helpers for embedding per-node variable details + per-variable time
series in resolver-driven HTML explorers.

Consumed by both make_map_resolver_ui.py and make_partition_map.py. The two
functions below produce the JSON-serialisable payloads that the side-panel
and time-series-plot JS in the HTML templates consume directly.
"""
from __future__ import annotations
from collections import defaultdict

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


def fetch_node_variables(scenario_id: str):
    """Return dict: node_id -> list of {id, type, related, kind, detail,
    value, source, date}. `value` is the resolved value at the latest
    observation date in the scenario; `kind` is 'TS' / 'F' / '-' depending on
    whether the variable has an explicit assignment under this scenario."""
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            WITH latest_d AS (
              SELECT MAX(observation_date) AS d
              FROM oil_network.scenario_resolved_values
              WHERE scenario_id = %s
            )
            SELECT v.node_id, v.variable_id, v.variable_type, v.related_node_id,
                   CASE WHEN va.timeseries_id IS NOT NULL THEN 'TS'
                        WHEN va.formula IS NOT NULL THEN 'F'
                        ELSE '-' END AS binding_kind,
                   COALESCE(va.timeseries_id, va.formula) AS binding_detail,
                   srv.value, srv.source, srv.observation_date
            FROM oil_network.variables v
            LEFT JOIN oil_network.variable_assignments va
              ON va.variable_id = v.variable_id AND va.scenario_id = %s
            LEFT JOIN latest_d ld ON TRUE
            LEFT JOIN oil_network.scenario_resolved_values srv
              ON srv.variable_id = v.variable_id
             AND srv.scenario_id = %s
             AND srv.observation_date = ld.d
            ORDER BY v.node_id, v.variable_type,
                     v.related_node_id NULLS FIRST, v.variable_id
        """, (scenario_id, scenario_id, scenario_id))
        out = defaultdict(list)
        for node_id, var_id, vtype, related, kind, detail, value, source, date in cur.fetchall():
            out[node_id].append({
                "id":      var_id,
                "type":    vtype,
                "related": related,
                "kind":    kind,
                "detail":  detail,
                "value":   float(value) if value is not None else None,
                "source":  source,
                "date":    str(date) if date else None,
            })
        return dict(out)


def fetch_series(scenario_id: str):
    """Return (dates, series) where:
      dates: list of "YYYY-MM-DD" strings (one per observation date in scenario)
      series: dict variable_id -> {"values": [...], "sources": [...]}
              Values aligned with `dates`; None where the resolver wrote NULL.
              Variables with NO non-null value at any date are OMITTED to keep
              the payload compact (~50% of variables are pure-latent).
    Aligned-array encoding is ~3x more compact than a list of {date,value}
    dicts after JSON minification.
    """
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT observation_date
            FROM oil_network.scenario_resolved_values
            WHERE scenario_id = %s
            ORDER BY observation_date
        """, (scenario_id,))
        dates = [str(r[0]) for r in cur.fetchall()]
        date_idx = {d: i for i, d in enumerate(dates)}

        cur.execute("""
            SELECT variable_id, observation_date, value, source
            FROM oil_network.scenario_resolved_values
            WHERE scenario_id = %s
            ORDER BY variable_id, observation_date
        """, (scenario_id,))
        series = {}
        for var_id, obs_date, value, source in cur.fetchall():
            s = series.get(var_id)
            if s is None:
                s = {"values": [None] * len(dates), "sources": [None] * len(dates),
                     "any_value": False}
                series[var_id] = s
            i = date_idx[str(obs_date)]
            if value is not None:
                s["values"][i] = float(value)
                s["any_value"] = True
            s["sources"][i] = source

        series = {vid: {"values": s["values"], "sources": s["sources"]}
                  for vid, s in series.items() if s["any_value"]}
        return dates, series
