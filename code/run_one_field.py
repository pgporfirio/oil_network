"""Standalone launcher for the production-sites explorer agent.

Runs ``explore_field_async`` on one field then persists the result. Use this
instead of executing the notebook via nbconvert — on Windows, the IPython
kernel switches to ``SelectorEventLoop`` for zmq compatibility, which breaks
``asyncio.create_subprocess_exec`` that the claude-agent-sdk needs to spawn
its bundled ``claude.exe``. Standalone Python keeps the default
ProactorEventLoop, so the SDK works.

Usage from ``Stage2/``::

    ..\\..\\.venv\\Scripts\\python.exe code\\run_one_field.py spraberry_trend_tx
    ..\\..\\.venv\\Scripts\\python.exe code\\run_one_field.py --model claude-haiku-4-5-20251001 prudhoe_bay_ak
    ..\\..\\.venv\\Scripts\\python.exe code\\run_one_field.py --force spraberry_trend_tx
    ..\\..\\.venv\\Scripts\\python.exe code\\run_one_field.py --force --replace spraberry_trend_tx

Persistence modes:

  default (append) — new agent run appends rows to the child tables and tags
                     each row with its producing run_id. Prior runs' rows are
                     preserved; the post-persist consistency scan flags any
                     "same logical entity recorded across multiple runs" via
                     simple natural keys (same operator + start_year, same
                     event_type + start_date, etc.), with findings written to
                     exploration_runs.inconsistencies as JSONB.

  --replace        — deletes every existing child row for this field_id
                     BEFORE the new run inserts. Useful when you want a
                     clean replacement and don't need prior runs' history.
                     The exploration_runs audit log is preserved either way.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from us_oil_production_sites_explorer import (  # noqa: E402
    already_explored,
    explore_field_async,
    list_fields,
    persist_to_db,
)


async def _run(field_id: str, model: str, force: bool, replace: bool) -> int:
    fields = {f["field_id"]: f for f in list_fields()}
    if field_id not in fields:
        print(f"ERROR: field_id {field_id!r} not in production_sites.field "
              f"(have {len(fields)} fields). Run load_seed_from_json() first.")
        return 2
    if not force and already_explored(field_id):
        print(f"already explored: {field_id}. Use --force to re-run.")
        return 0

    f = fields[field_id]
    mode = "REPLACE (deletes prior rows for this field)" if replace else "APPEND (preserves prior runs)"
    print(f"exploring {field_id}: {f['name']}  (basin={f['basin']!r}  model={model!r}  persist={mode})")
    t0 = time.time()
    buf, meta = await explore_field_async(f, model=model)
    elapsed = time.time() - t0
    persist_to_db(buf, f, meta, replace_existing=replace)

    print(
        f"\n  status        : {meta['status']}"
        f"\n  tool_calls    : {meta['tool_calls']}"
        f"\n  cost_usd      : ${meta.get('cost_usd', 0):.3f}"
        f"\n  elapsed       : {elapsed:.0f}s ({elapsed/60:.1f} min)"
        f"\n  operators     : {len(buf['operators'])}"
        f"\n  grades        : {len(buf['grades'])}"
        f"\n  production    : {len(buf['production'])}"
        f"\n  reserves      : {len(buf['reserves'])}"
        f"\n  events        : {len(buf['events'])}"
        f"\n  logistics     : {len(buf['logistics'])}"
        f"\n  sources       : {len(buf['sources'])}"
    )
    if meta.get("error"):
        print(f"  error         : {meta['error']}")
    if buf.get("summary"):
        print(f"\nsummary: {buf['summary']}")
    return 0 if meta["status"] == "success" else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("field_id", help="field_id slug from production_sites.field")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--force", action="store_true",
                    help="re-run even if exploration_runs already shows success")
    ap.add_argument("--replace", action="store_true",
                    help="delete prior rows for this field before inserting "
                         "(default: append + tag with run_id, preserve prior runs)")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args.field_id, args.model, args.force, args.replace)))


if __name__ == "__main__":
    main()
