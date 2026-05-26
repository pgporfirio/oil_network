"""HTML orchestrator: regenerate the visualisation HTMLs in the right order,
record each artefact in the audit table, and skip what is already current.

Usage:
    python regenerate_htmls.py                # regenerate only stale HTMLs
    python regenerate_htmls.py --force        # regenerate every HTML
    python regenerate_htmls.py --list         # status only; no rebuilding
    python regenerate_htmls.py --views balance,partition_map   # subset

How "stale" works:
  Each generated HTML embeds a metadata beacon naming the resolver run_id that
  produced it. This script reads the latest run_id from
  `oil_network.scenario_resolver_runs`; any HTML whose embedded run_id is
  smaller (or whose beacon is missing) is regenerated. The audit table
  `scenario_html_artefacts` records every regeneration.

Renderers registered:
  balance, hierarchy, map, partition_map, node_neighbors_map
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

from render_utils import (ensure_table, latest_run_id, extract_metadata,
                          DB)
from paths import HTML_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCENARIO = "starter_us_crude_2015_2025"

# HTML output directory (clean/outputs/html/) — the orchestrator scans here for
# existing artefacts and the renderers write here.
ROOT = HTML_DIR

# (view_name, output_file, generator_module)
RENDERERS = [
    # Foundational maps that read the new layered views directly.
    ("partition_map",         "oil_network_partition_map.html",         "make_partition_map"),
    ("node_neighbors_map",    "oil_network_node_neighbors.html",        "make_node_neighbors_map"),
    # Resolver-driven UIs (canonical going forward; inline-SQL ancestors are
    # being retired).
    ("balance",               "oil_network_balance_resolver.html",      "make_balance_resolver_ui"),
    ("hierarchy",             "oil_network_hierarchy_resolver.html",    "make_hierarchy_resolver_ui"),
    ("map",                   "oil_network_map_resolver.html",          "make_map_resolver_ui"),
]


def staleness(latest: int, view_name: str, out_path: Path) -> tuple[str, dict | None]:
    """Return ('missing'|'stale'|'current'|'unknown', metadata_or_None)."""
    if not out_path.exists():
        return "missing", None
    meta = extract_metadata(out_path)
    if meta is None:
        return "unknown", None  # file exists but no beacon — treat as stale
    embedded = meta.get("run_id")
    if embedded is None:
        return "unknown", meta
    if embedded < latest:
        return "stale", meta
    return "current", meta


def regenerate_one(module_name: str, view_name: str) -> tuple[bool, str]:
    """Import the module and call its main(). Returns (ok, message)."""
    try:
        mod = importlib.import_module(module_name)
        # Force a reload in case the module was imported earlier in this run
        # (e.g. resolver_ui modules import their base).
        importlib.reload(mod)
        t0 = time.perf_counter()
        mod.main()
        dt = (time.perf_counter() - t0) * 1000
        return True, f"ok ({dt:.0f} ms)"
    except Exception as e:
        return False, f"FAIL: {type(e).__name__}: {e}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true",
                   help="regenerate every HTML, ignoring beacon staleness")
    p.add_argument("--list", action="store_true",
                   help="print status table only; do not regenerate")
    p.add_argument("--views", default=None,
                   help="comma-separated subset of view names to consider")
    args = p.parse_args()

    ensure_table()  # belt-and-braces

    latest = latest_run_id(SCENARIO)
    print(f"Scenario: {SCENARIO}")
    print(f"Latest resolver run_id: {latest}\n")

    subset = set(args.views.split(",")) if args.views else None

    # Status table
    print(f"  {'view':22s} {'file':42s} {'status':12s} {'embedded_run':>14s}")
    print("  " + "-" * 92)
    work = []
    for view_name, out_file, module in RENDERERS:
        if subset and view_name not in subset:
            continue
        out_path = ROOT / out_file
        status, meta = staleness(latest, view_name, out_path)
        embedded = meta["run_id"] if meta else "—"
        marker = {"missing": "✗", "stale": "↻", "current": "✓", "unknown": "?"}[status]
        print(f"  {marker} {view_name:22s} {out_file:42s} {status:12s} {str(embedded):>14s}")
        if status != "current" or args.force:
            work.append((view_name, module))

    print()
    if args.list:
        print("[--list] no regeneration performed.")
        return

    if not work:
        print("All up to date. Use --force to rebuild anyway.")
        return

    print(f"Regenerating {len(work)} view(s):\n")
    for view_name, module in work:
        print(f"--- {view_name} ({module}) ---")
        ok, msg = regenerate_one(module, view_name)
        print(f"    {msg}\n")

    print("Done.")


if __name__ == "__main__":
    main()
