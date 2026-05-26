"""Quick standalone test of query_eia_production in the production-sites explorer."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from us_oil_production_sites_explorer import query_eia_production


async def main() -> None:
    cases = [
        {"asset_id": "permian",            "name_filter": "", "since_date": "2018-01-01"},
        {"asset_id": "bakken_nd",          "name_filter": "", "since_date": "2018-01-01"},
        {"asset_id": "alaska_north_slope", "name_filter": "", "since_date": "2018-01-01"},
        {"asset_id": "gulf_of_america",    "name_filter": "", "since_date": "2018-01-01"},
        {"asset_id": "",                   "name_filter": "Eagle Ford",         "since_date": "2018-01-01"},
        {"asset_id": "",                   "name_filter": "California state",   "since_date": "2018-01-01"},
    ]
    for c in cases:
        result = await query_eia_production.handler(c)
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
