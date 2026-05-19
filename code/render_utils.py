"""Shared helpers for HTML renderers.

  * `latest_run_id(scenario)`  -- the freshest resolver run for a scenario
  * `metadata_html(scenario, run_id, view_name)` -- the <meta> block that
        renderers embed in the HTML <head>, encoding what produced the file
  * `extract_run_id(path)`      -- read the embedded run_id back from a file
  * `record_artefact(scenario, run_id, view, path, file_size)` -- write an
        audit row to scenario_html_artefacts
  * `ensure_table()` -- idempotent DDL for the artefact table
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# ---------------------------------------------------------------------------
# DDL: artefact audit table
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS oil_network.scenario_html_artefacts (
    artefact_id      BIGSERIAL PRIMARY KEY,
    scenario_id      TEXT NOT NULL REFERENCES oil_network.scenarios(scenario_id) ON DELETE CASCADE,
    run_id           BIGINT REFERENCES oil_network.scenario_resolver_runs(run_id) ON DELETE SET NULL,
    view_name        TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    file_size_bytes  INTEGER,
    generated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS ix_html_artefacts_scenario_run
    ON oil_network.scenario_html_artefacts(scenario_id, run_id DESC, view_name);
CREATE INDEX IF NOT EXISTS ix_html_artefacts_view
    ON oil_network.scenario_html_artefacts(view_name, generated_at DESC);

COMMENT ON TABLE oil_network.scenario_html_artefacts IS
'Audit log: one row per HTML regeneration. Lets you answer "which run produced this file?" and "is this view stale?" without parsing the file.';
"""


def ensure_table(conn=None):
    own = conn is None
    if own:
        conn = psycopg2.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# Run-id lookup
# ---------------------------------------------------------------------------

def latest_run_id(scenario_id: str, conn=None) -> Optional[int]:
    own = conn is None
    if own:
        conn = psycopg2.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id FROM oil_network.scenario_resolver_runs
                WHERE scenario_id = %s AND completed_at IS NOT NULL
                ORDER BY started_at DESC LIMIT 1
                """,
                (scenario_id,),
            )
            r = cur.fetchone()
            return r[0] if r else None
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# Metadata block embedded in HTML <head>
# ---------------------------------------------------------------------------

def metadata_html(scenario_id: str, run_id: Optional[int],
                   view_name: str, extra: Optional[dict] = None) -> str:
    """Return a block of <meta> tags + a JSON-comment beacon for fast parsing.

    The beacon `<!-- oilnet-artefact: {...} -->` is the canonical machine-
    readable provenance marker; the <meta> tags duplicate the same info for
    browser-side inspection.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "scenario": scenario_id,
        "run_id": run_id,
        "view": view_name,
        "generated_at": now,
    }
    if extra:
        meta.update(extra)

    metas = [
        f'<meta name="oilnet:scenario" content="{scenario_id}">',
        f'<meta name="oilnet:resolver_run_id" content="{run_id}">',
        f'<meta name="oilnet:view" content="{view_name}">',
        f'<meta name="oilnet:generated_at" content="{now}">',
    ]
    import json as _json
    beacon = f'<!-- oilnet-artefact: {_json.dumps(meta, separators=(",", ":"))} -->'
    return beacon + "\n" + "\n".join(metas)


# ---------------------------------------------------------------------------
# Extract metadata back from a file
# ---------------------------------------------------------------------------

BEACON_RE = re.compile(r"<!-- oilnet-artefact: (\{.*?\}) -->")


def extract_metadata(path: Path) -> Optional[dict]:
    """Read the beacon from a generated HTML. Returns None if missing."""
    p = Path(path)
    if not p.exists():
        return None
    # We only need to inspect the head, so read the first ~4 KB
    try:
        text = p.read_text(encoding="utf-8", errors="replace")[:8192]
    except Exception:
        return None
    m = BEACON_RE.search(text)
    if not m:
        return None
    import json as _json
    try:
        return _json.loads(m.group(1))
    except Exception:
        return None


def extract_run_id(path: Path) -> Optional[int]:
    meta = extract_metadata(path)
    return meta.get("run_id") if meta else None


# ---------------------------------------------------------------------------
# Audit recording
# ---------------------------------------------------------------------------

def record_artefact(scenario_id: str, run_id: Optional[int], view_name: str,
                     file_path: Path, file_size_bytes: int,
                     notes: Optional[str] = None,
                     conn=None) -> int:
    own = conn is None
    if own:
        conn = psycopg2.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oil_network.scenario_html_artefacts
                    (scenario_id, run_id, view_name, file_path, file_size_bytes, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING artefact_id
                """,
                (scenario_id, run_id, view_name, str(file_path),
                 file_size_bytes, notes),
            )
            aid = cur.fetchone()[0]
        conn.commit()
        return aid
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# One-stop render-write helper used by all generators
# ---------------------------------------------------------------------------

def write_html(content: str, output_path: Path, scenario_id: str,
                run_id: Optional[int], view_name: str,
                notes: Optional[str] = None) -> int:
    """Inject metadata into <head>, write the file, record an audit row.

    Returns the artefact_id of the recorded row.
    """
    meta_block = metadata_html(scenario_id, run_id, view_name)
    # Insert immediately after the opening <head>
    injected = re.sub(r"(<head[^>]*>)",
                      r"\1\n" + meta_block,
                      content, count=1, flags=re.IGNORECASE)
    out = Path(output_path)
    out.write_text(injected, encoding="utf-8")
    return record_artefact(
        scenario_id=scenario_id, run_id=run_id, view_name=view_name,
        file_path=out, file_size_bytes=out.stat().st_size, notes=notes,
    )


# ---------------------------------------------------------------------------
# Module bootstrap: ensure the audit table exists on import
# ---------------------------------------------------------------------------

ensure_table()
