"""Refresh helper for the materialised views.

Two levels of refresh:

  --structural   refreshes layers 2 and 3 (formula_input_links, aggregation_edges,
                 flow_edges, partition_tree, node_status). Run after any migration
                 that touches `variable_assignments` or `variables`.

  --analytic     refreshes layer 4 (node_balance_check, aggregation_consistency,
                 inventory_changes, aggregate_balance). Run after each resolver
                 pass.

  (no flag)      refreshes both, in layer order. Use this if unsure.

All refreshes use CONCURRENTLY so reads are not blocked.

CLI:
    python refresh_views.py                 # both
    python refresh_views.py --structural    # only L2/L3
    python refresh_views.py --analytic      # only L4

Library use:
    from refresh_views import refresh_structural, refresh_analytic, refresh_all
    refresh_analytic(conn)   # call this from resolve_scenario.py after each run
"""
from __future__ import annotations

import argparse
import sys
import time

import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


STRUCTURAL_VIEWS = [
    "v_formula_input_links",  # L2a
    "v_aggregation_edges",    # L2b
    "v_flow_edges",           # L2c
    "v_partition_tree",       # L3a (depends on L2b)
    "v_node_status",          # L3b
]

ANALYTIC_VIEWS = [
    "v_node_balance_check",     # L4a
    "v_aggregation_consistency", # L4b (depends on L3a)
    "v_inventory_changes",      # L4c
    "v_aggregate_balance",      # L4d (depends on L4a)
    "v_node_pcisob",            # L4e (per-node per-date balance aggregates)
]


def _refresh_one(cur, view, concurrently=True, verbose=True):
    qual = "CONCURRENTLY " if concurrently else ""
    sql = f"REFRESH MATERIALIZED VIEW {qual}oil_network.{view}"
    t0 = time.perf_counter()
    cur.execute(sql)
    elapsed = (time.perf_counter() - t0) * 1000
    if verbose:
        cur.execute(f"SELECT COUNT(*) FROM oil_network.{view}")
        n = cur.fetchone()[0]
        print(f"  ✓ {view:32s} {n:>8,} rows  ({elapsed:.0f} ms)")


def refresh_structural(conn=None, verbose=True):
    """Refresh layer 2 + layer 3 views (structural / schema-data-dependent)."""
    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**DB)
    try:
        conn.autocommit = True  # CONCURRENTLY requires being outside a tx block
        with conn.cursor() as cur:
            if verbose:
                print("[refresh] structural views (L2 + L3):")
            for v in STRUCTURAL_VIEWS:
                _refresh_one(cur, v, verbose=verbose)
    finally:
        if own_conn:
            conn.close()


def refresh_analytic(conn=None, verbose=True):
    """Refresh layer 4 views (analytic / resolved-values-dependent)."""
    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**DB)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            if verbose:
                print("[refresh] analytic views (L4):")
            for v in ANALYTIC_VIEWS:
                _refresh_one(cur, v, verbose=verbose)
    finally:
        if own_conn:
            conn.close()


def refresh_all(conn=None, verbose=True):
    refresh_structural(conn, verbose=verbose)
    refresh_analytic(conn, verbose=verbose)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--structural", action="store_true", help="only L2/L3")
    p.add_argument("--analytic",   action="store_true", help="only L4")
    args = p.parse_args()
    if args.structural and not args.analytic:
        refresh_structural()
    elif args.analytic and not args.structural:
        refresh_analytic()
    else:
        refresh_all()


if __name__ == "__main__":
    main()
