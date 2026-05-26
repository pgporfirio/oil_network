"""Re-run the refinery explorer on ref_american_bradford with the fixed EIA tool.

Clears Bradford's prior rows (so resume-aware skip doesn't fire), runs the agent,
persists, and prints a one-line summary suitable for the chat.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import psycopg2

from us_refinery_explorer import (
    list_us_refineries, explore_refinery_async, persist_to_db, _conn,
)


TARGET = "ref_american_bradford"


def _wipe_prior(rid: str) -> None:
    """Delete every refineries.* row for this refinery so the rerun is clean."""
    with _conn() as conn, conn.cursor() as cur:
        # All children FK-cascade off refineries.refinery — one delete is enough,
        # except exploration_runs which we keep for cost history. Hmm — but the
        # `already_explored` check looks at exploration_runs, so if we keep the
        # old 'success' row it will skip. Easiest: delete the master row
        # (cascades everywhere) and let the agent recreate everything.
        cur.execute(
            "DELETE FROM refineries.refinery WHERE refinery_id = %s", (rid,)
        )
        print(f"  wiped {cur.rowcount} master row(s) (cascade purged children).")


async def main() -> None:
    refs = list_us_refineries()
    bradford = next((r for r in refs if r["refinery_id"] == TARGET), None)
    if bradford is None:
        print(f"ERROR: {TARGET} not found in oil_network.nodes.")
        sys.exit(1)

    print(f"Re-running {TARGET}: {bradford['name']} ({bradford['duoarea_code']})")
    _wipe_prior(TARGET)

    t0 = time.time()
    buf, meta = await explore_refinery_async(bradford)
    elapsed = time.time() - t0

    persist_to_db(buf, bradford, meta)

    print()
    print("=" * 60)
    print(f"  status:     {meta.get('status')}")
    print(f"  elapsed:    {elapsed:6.1f} s  ({elapsed/60:.1f} min)")
    print(f"  tool_calls: {meta.get('tool_calls')}")
    print(f"  tokens_in:  {meta.get('tokens_in')}")
    print(f"  tokens_out: {meta.get('tokens_out')}")
    print(f"  cost_usd:   {meta.get('cost_usd')}")
    print(f"  units:      {len(buf.get('units', []))}")
    print(f"  slate:      {len(buf.get('slate', []))}")
    print(f"  events:     {len(buf.get('events', []))}")
    print(f"  financials: {len(buf.get('financials', []))}")
    print(f"  monthly:    {len(buf.get('monthly', []))}")
    print(f"  sources:    {len(buf.get('sources', []))}")
    print(f"  summary:    {buf.get('summary')}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
