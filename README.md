# oil_network

Asset-centric temporal graph model of US crude-oil logistics. Master thesis project — Pedro Porfirio, NOVA IMS.

See `claude/CLAUDE.md` for the design principles and `claude/PROJECT_STATE.md` for current state and outstanding work.

## Fresh-machine setup

```powershell
git clone <this-private-repo-url> oil-network
cd oil-network
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # PowerShell; on macOS/Linux use: source .venv/bin/activate
pip install jupyter python-dotenv
jupyter notebook setup.ipynb
```

Then run each cell of `setup.ipynb` top-to-bottom. It installs `requirements.txt`, captures your EIA API key + Postgres credentials into a gitignored `.env`, provisions the `eia_user` role and `eia_crude` database, and runs the master orchestrator end-to-end.

Prerequisites the notebook does not install for you: **Python 3.11+** and a local **PostgreSQL 14+** running on `localhost:5432`. On Windows the EDB installer is the simplest path; on macOS `brew install postgresql@16 && brew services start postgresql@16`.

Get a free EIA Open Data API key at https://www.eia.gov/opendata/ before running cell 3.

## Layout

```
clean/
├── claude/        # design docs (CLAUDE.md, HANDOVER.md, PROJECT_STATE.md, time_log.md)
├── code/          # all Python + notebooks (45 .py, 10 .ipynb; flat layout)
│   ├── data/      # reference data (refcap25.xlsx)
│   └── old/       # archival, nothing active reads here
├── config/        # asset_graph.json — the seed
├── outputs/
│   ├── html/      # 5 visualisation HTMLs
│   └── docs/      # thesis drafts, PDFs, diagrams, reference papers
├── setup.ipynb    # fresh-machine bootstrap
└── requirements.txt
```

## Day-to-day

Re-run the full pipeline:
```powershell
cd code
..\.venv\Scripts\jupyter.exe nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 initialize_oil_network.ipynb
```

Re-generate the 5 HTML explorers:
```powershell
..\.venv\Scripts\python.exe regenerate_htmls.py --force
```

The orchestrator is idempotent — `DROP SCHEMA` → full rebuild in ~4 minutes.
