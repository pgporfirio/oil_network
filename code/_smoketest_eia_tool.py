"""Quick standalone test for the fixed query_eia_timeseries tool.

Calls the async tool directly with Bradford's duoarea (RAP, Appalachian No.1)
and prints whether the SQL works + how many rows come back.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make local imports work whether run from Stage2/ or from anywhere.
sys.path.insert(0, str(Path(__file__).parent))

from us_refinery_explorer import query_eia_timeseries


async def main() -> None:
    cases = [
        {"duoarea_code": "RAP", "name_filter": "crude inputs", "since_date": "2018-01-01"},
        {"duoarea_code": "R10", "name_filter": "crude",        "since_date": "2018-01-01"},
        {"duoarea_code": "R3B", "name_filter": "crude inputs", "since_date": "2020-01-01"},
        {"duoarea_code": "RAP", "name_filter": "operating capacity", "since_date": "2018-01-01"},
    ]
    for c in cases:
        result = await query_eia_timeseries.handler(c)
        text = result["content"][0]["text"]
        try:
            payload = json.loads(text)
            print(f"\n=== {c}")
            print(f"   series_found: {payload.get('series_found')}")
            print(f"   row_count:    {payload.get('row_count')}")
            if payload.get("rows"):
                print(f"   first row:    {payload['rows'][0]}")
                print(f"   last  row:    {payload['rows'][-1]}")
        except json.JSONDecodeError:
            print(f"\n=== {c}")
            print(f"   non-JSON output: {text[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
