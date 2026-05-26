"""Re-run the Bradford smoke test after the EIA-tool fix.

Forces re-exploration (overwrites the prior buffer; previous data stays put thanks to UPSERTs).
Prints headline stats so we can compare to the original $4.41 / 185-tool-call baseline.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from us_refinery_explorer import (
    list_us_refineries,
    explore_refinery_async,
    persist_to_db,
)


TARGET = "ref_american_bradford"


async def main() -> None:
    refs = {r["refinery_id"]: r for r in list_us_refineries()}
    if TARGET not in refs:
        print(f"ERROR: {TARGET} not in DB")
        sys.exit(1)
    r = refs[TARGET]
    print(f"[{time.strftime('%H:%M:%S')}] Re-running smoke test on {TARGET}")
    print(f"   corporation: {r.get('corporation')}")
    print(f"   site:        {r.get('site')}")
    print(f"   duoarea:     {r.get('duoarea_code')}  ({r.get('rdist_label')})")
    print(f"   capacity:    {r.get('capacity_bpd')} bpd")
    print()

    t0 = time.time()
    buf, meta = await explore_refinery_async(r)
    persist_to_db(buf, r, meta)
    dt = time.time() - t0

    print()
    print(f"[{time.strftime('%H:%M:%S')}] === RESULT ===")
    print(f"   status:      {meta.get('status')}")
    print(f"   duration:    {dt/60:.1f} min ({dt:.0f}s)")
    print(f"   tool_calls:  {meta.get('tool_calls')}")
    print(f"   tokens_in:   {meta.get('tokens_in')}")
    print(f"   tokens_out:  {meta.get('tokens_out')}")
    print(f"   cost_usd:    ${meta.get('cost_usd'):.4f}")
    print(f"   error:       {meta.get('error')}")
    print()
    print(f"   buffer counts:")
    print(f"     units:      {len(buf.get('units', []))}")
    print(f"     slate:      {len(buf.get('slate', []))}")
    print(f"     events:     {len(buf.get('events', []))}")
    print(f"     financials: {len(buf.get('financials', []))}")
    print(f"     monthly:    {len(buf.get('monthly', []))}")
    print(f"     sources:    {len(buf.get('sources', []))}")
    print(f"   summary:     {buf.get('summary')}")


if __name__ == "__main__":
    asyncio.run(main())
