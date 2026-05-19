"""Headless smoke test of mass_balance_explorer.

Runs the Streamlit script through AppTest at three node selections + a date,
captures errors, prints a summary of the mass-balance numbers visible in the UI.
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from streamlit.testing.v1 import AppTest

SCRIPT = Path(__file__).parent / "mass_balance_explorer.py"
TARGET = date(2024, 12, 1)


def run_at(node: str) -> AppTest:
    at = AppTest.from_file(str(SCRIPT), default_timeout=60)
    at.session_state["selected_node"] = node
    at.run()
    if at.exception:
        print(f"[{node}] EXCEPTIONS:")
        for e in at.exception:
            print(f"  {e}")
        sys.exit(1)
    return at


def summarise(at: AppTest, node: str):
    # Find every metric in the script output and dump label/value
    print(f"\n=== {node} ===")
    metrics = list(at.metric)
    for m in metrics:
        print(f"  {m.label:30s}  {m.value}")
    err = list(at.error)
    if err:
        print("  ERRORS:")
        for e in err:
            print(f"    {e.value}")


# Sweep one node of each major type to catch edge cases.
for node in [
    "usa_view",                          # region aggregate, all primitives TS-bound
    "padd2_view",                        # PADD, closure inconsistency
    "permian_tx",                        # physical production, mostly latent
    "cushing_hub",                       # observed inventory, latent flows
    "district_R3B_refining_view",        # TS-bound consumption only
    "ref_whiting",                       # named refinery, all latent
    "padd5_view",                        # PADD with known data gap (P5 outflow sparse)
]:
    at = run_at(node)
    summarise(at, node)

print("\nAll three node selections rendered without exceptions.")
