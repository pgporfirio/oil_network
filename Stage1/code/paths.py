"""Project paths.

Single source of truth for filesystem locations. Every script that reads or
writes a file by absolute path should import the relevant constant from here
instead of hand-rolling the path. Moving the project (or splitting `clean/`
into separate repos for code/config/outputs in production) becomes a one-line
edit here.

Layout:

    clean/
    |-- claude/                 # CLAUDE.md, HANDOVER.md, design docs (project memory)
    |-- code/                   # all .py + .ipynb (this directory)
    |   |-- data/               # reference data used by scripts (refcap25.xlsx etc.)
    |   `-- paths.py
    |-- config/                 # JSON seed files (asset_graph.json)
    `-- outputs/
        |-- html/               # generated visualisation HTMLs
        `-- docs/               # generated + manually edited docs (PDF, DOCX, diagrams)

The `old/` directory at the repo level (sibling of `clean/`) holds historical
artefacts: thesis-draft history, retired scripts, backup JSONs. Nothing in
`old/` is read by active code.
"""
from __future__ import annotations

from pathlib import Path


# clean/code/paths.py -> clean/code -> clean
CODE_DIR    = Path(__file__).resolve().parent
DATA_DIR    = CODE_DIR / "data"
CLEAN_ROOT  = CODE_DIR.parent
CLAUDE_DIR  = CLEAN_ROOT / "claude"
CONFIG_DIR  = CLEAN_ROOT / "config"
OUTPUTS_DIR = CLEAN_ROOT / "outputs"
HTML_DIR    = OUTPUTS_DIR / "html"
DOCS_DIR    = OUTPUTS_DIR / "docs"

# Repository root (clean's parent, which sits next to `old/`)
REPO_ROOT   = CLEAN_ROOT.parent

# Common file paths
ASSET_GRAPH_JSON = CONFIG_DIR / "asset_graph.json"


# Convenience: ensure output directories exist (idempotent)
def ensure_dirs() -> None:
    for d in (HTML_DIR, DOCS_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(f"CODE_DIR    {CODE_DIR}")
    print(f"DATA_DIR    {DATA_DIR}")
    print(f"CONFIG_DIR  {CONFIG_DIR}    asset_graph.json exists: {ASSET_GRAPH_JSON.exists()}")
    print(f"CLAUDE_DIR  {CLAUDE_DIR}")
    print(f"HTML_DIR    {HTML_DIR}")
    print(f"DOCS_DIR    {DOCS_DIR}")
