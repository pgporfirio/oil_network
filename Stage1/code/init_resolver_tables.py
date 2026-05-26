"""Create the resolver audit tables empty.

Extracted from resolve_scenario.py so the orchestrator can create
`scenario_resolver_runs` + `scenario_resolved_values` BEFORE thirteenth_pass_views.py
runs (the L4 materialised views in thirteenth_pass reference
`scenario_resolved_values` at definition time, so the table must exist even
if empty).

Idempotent — every statement uses IF NOT EXISTS.
"""
from __future__ import annotations
import psycopg2

from resolve_scenario import DDL, DB


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
    print("scenario_resolver_runs + scenario_resolved_values created (or already existed)")


if __name__ == "__main__":
    main()
