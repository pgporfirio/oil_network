# Restore the project to `stage_1_complete`

A runbook. Follow either the **fast path** (Option A — restore from snapshot, no EIA calls, takes seconds) or the **fresh-rebuild path** (Option B — re-run the orchestrator, takes 6–8 min, proves reproducibility). Both end at the same state.

The marker is the git tag **`stage_1_complete`** on `git@github.com:pgporfirio/oil_network_clean.git`, plus the DB snapshot file at `Oil Network Project/snapshots/oil_network_stage_1_complete.dump`.

---

## What "stage 1" means

Code frozen on 2026-05-18 for thesis defence preparation. At this tag the project state is:

- **Thesis:** `outputs/docs/Master_Thesis_Pedro_Porfirio_v37.{docx,pdf}` (~42.8k words, 1.23 MB PDF, 128 pages).
- **Schema:** 251 nodes (217 physical + 34 abstract), 1,870 variables, 975 explicit variable assignments, 90 active TS bindings, 68,793 vintaged timeseries rows, 291,564 resolved rows for the starter scenario, 215 capacity rows (31 time-versioned), 19 commodities (incl. crude-grade registry) + 18 hierarchy edges.
- **Views:** 12 materialised views (`v_flow_edges`, `v_aggregation_edges`, `v_partition_tree`, `v_node_status`, `v_partition_sums`, `v_partition_intra_flows`, `v_node_balance_check`, `v_aggregation_consistency`, `v_inventory_changes`, `v_aggregate_balance`, `v_node_pcisob`, `v_formula_input_links`) + 5 regular views.
- **Resolver:** 0 unresolved, dispatch = 90 observed + 542 zero + 767 latent + 451 arithmetic + 5 sum + 15 reverse_mirror.
- **Audits:** 0 capacity violations, 0 TS-binding collisions, 3,411 aggregation_consistency ok + 3,453 partial_coverage + 0 inconsistent.
- **HTMLs:** 5 explorers under `outputs/html/` (balance, hierarchy, map, partition, node-neighbours).
- **Reference PDFs:** `Design_Principles.pdf`, `Resolver_Walkthrough.pdf`, `Scenario_Construction.pdf`, `Graph_Construction.pdf`.

---

## Prerequisites

Same for both paths.

| Requirement | Notes |
|---|---|
| **Git** | any recent version |
| **PostgreSQL 18** | (the snapshot was taken from PG 18; older majors may refuse a custom-format restore — easiest to install PG 18) |
| **Python 3.11** | the project venv pins this |
| **EIA API key** | **Option B only.** Free, register at `https://www.eia.gov/opendata/`. Held in a gitignored `.env` — never commit it. |
| **SSH access to GitHub** | the remote is private, configured for `git@github.com:pgporfirio/oil_network_clean.git` |

On Windows pg_dump / pg_restore live at `C:\Program Files\PostgreSQL\18\bin\`. Either add that to PATH or use the absolute path in the commands below.

---

## Option A — fast path (restore from snapshot)

Use this when you are **continuing work** on a machine you already have set up, or when you want to skip the EIA-staging step on a fresh machine. The snapshot rehydrates the entire `oil_network` schema in seconds.

### A.1  Clone the repo and check out the tag

```bash
git clone git@github.com:pgporfirio/oil_network_clean.git
cd oil_network_clean
git checkout stage_1_complete
```

### A.2  Create the database and role (skip if it already exists)

```bash
# Postgres superuser shell (psql -U postgres)
CREATE ROLE eia_user WITH LOGIN PASSWORD 'eia_password';
CREATE DATABASE eia_crude OWNER eia_user;
\q
```

### A.3  Locate the snapshot

The dump lives **outside the repo** at:

```
Oil Network Project/snapshots/oil_network_stage_1_complete.dump
```

On Pedro's OneDrive this syncs automatically. On a fresh machine, transfer the `.dump` file from OneDrive (or any durable location) onto disk first. The path can be anywhere.

### A.4  Restore the schema

```bash
# On Windows (PowerShell)
$env:PGPASSWORD = 'eia_password'
& 'C:\Program Files\PostgreSQL\18\bin\pg_restore.exe' `
    -h localhost -U eia_user -d eia_crude -n oil_network `
    --clean --if-exists `
    "$env:USERPROFILE\OneDrive - Jabuticaba\Oil Network Project\snapshots\oil_network_stage_1_complete.dump"

# On macOS / Linux (bash)
PGPASSWORD=eia_password pg_restore \
    -h localhost -U eia_user -d eia_crude -n oil_network \
    --clean --if-exists \
    "/path/to/oil_network_stage_1_complete.dump"
```

`--clean --if-exists` drops the existing `oil_network` schema before restoring, so this command is idempotent and safe to re-run.

### A.5  Set up the Python environment

```bash
# From the repo root
cd Thesis/clean
python -m venv ../../.venv
../../.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# OR: ../../.venv/bin/python -m pip install -r requirements.txt     # macOS/Linux
```

### A.6  Verify

Jump to [§ Verification](#verification) below.

---

## Option B — fresh-rebuild path (orchestrator from scratch)

Use this when you want to **prove the rebuild works** (no DB snapshot needed) or when the snapshot is unavailable. Slower (6–8 min) and requires a valid EIA API key.

### B.1  Clone and check out the tag

```bash
git clone git@github.com:pgporfirio/oil_network_clean.git
cd oil_network_clean
git checkout stage_1_complete
```

### B.2  Run `setup.ipynb`

```bash
cd Thesis/clean
jupyter notebook setup.ipynb     # or open in VSCode and Run All
```

`setup.ipynb` does everything end-to-end:

1. Creates the Python venv at `../../.venv`.
2. `pip install -r requirements.txt`.
3. Prompts for the EIA API key + Postgres credentials, writes them to a gitignored `.env`.
4. Provisions the `eia_user` role and `eia_crude` database.
5. Runs the master orchestrator (`code/initialize_oil_network.ipynb`), which is 4 stages and 38 steps inside stage 4:
   - **Stage 1** — schema DDL + asset graph load from `config/asset_graph.json`
   - **Stage 2** — typed metadata layer
   - **Stage 3** — EIA timeseries ingestion (this is the slow step; ~3–4 min)
   - **Stage 4** — variable assignments, 23 migrations, view creation, resolver, audits
6. Regenerates the 5 HTML explorers under `outputs/html/`.

When `setup.ipynb` finishes, the database and the rendered outputs are at the same state as the snapshot.

### B.3  Verify

Jump to [§ Verification](#verification) below.

---

## Verification

Same for both paths. Run this from the repo root with the venv activated:

```bash
../../.venv/Scripts/python.exe code/verify_state.py
```

The expected output ends with:

```
Headline counts:
  assets                              251
  nodes                               251
  variables                           1,870
  variable_assignments                 975
  timeseries_data                  68,793
  scenario_resolved_values        291,564

Latest resolver run:
  dispatch: {'observed': 90, 'zero': 542, 'latent': 767,
             'arithmetic': 451, 'sum': 5, 'reverse_mirror': 15,
             'unresolved': 0}

Audits:
  capacity violations:                  0
  TS-binding collisions:                0
  v_aggregation_consistency:    3,411 ok / 3,453 partial / 0 inconsistent
```

If any of those numbers disagree by more than a rounding difference, something has drifted. Most likely culprit: the EIA API published a vintage you didn't have when the snapshot was taken, which can change `timeseries_data` row counts under Option B. The schema and resolved-row counts should still match exactly.

For a quick spot-check without running `verify_state.py`:

```sql
-- psql -U eia_user -d eia_crude
SELECT 'assets', count(*) FROM oil_network.assets
UNION ALL SELECT 'variables',    count(*) FROM oil_network.variables
UNION ALL SELECT 'resolved',     count(*) FROM oil_network.scenario_resolved_values
UNION ALL SELECT 'commodities',  count(*) FROM oil_network.commodities
UNION ALL SELECT 'commodity_hierarchy', count(*) FROM oil_network.commodity_hierarchy
ORDER BY 1;
```

Expected:

| label | count |
|---|---:|
| assets | 251 |
| commodities | 19 |
| commodity_hierarchy | 18 |
| resolved | 291,564 |
| variables | 1,870 |

---

## What's where (reference)

```
oil_network_clean/                                    ← git repo root
└── Thesis/clean/                                     ← the active project
    ├── README.md
    ├── RESTORE_STAGE_1.md                            ← this file
    ├── setup.ipynb                                   ← fresh-machine bootstrap
    ├── requirements.txt
    ├── .env.example                                  ← template; copy to .env
    ├── claude/
    │   ├── CLAUDE.md                                 ← project context + design principles
    │   ├── PROJECT_STATE.md                          ← live numbers + outstanding work
    │   ├── HANDOVER.md                               ← pass-by-pass narrative history
    │   ├── NOTEBOOKS.md                              ← orchestrator chain documentation
    │   └── time_log.md
    ├── code/
    │   ├── initialize_oil_network.ipynb              ← master orchestrator (4 stages)
    │   ├── initialize_oil_network_assignments.ipynb  ← stage 4 (38 steps)
    │   ├── resolve_scenario.py                       ← primary resolver
    │   ├── recursive_resolver.py                     ← fixed-point alternative
    │   ├── verify_state.py                           ← post-rebuild sanity check
    │   ├── regenerate_htmls.py                       ← rebuild the 5 HTMLs
    │   └── migrations/                               ← 23 one-shot scripts
    ├── config/
    │   └── asset_graph.json                          ← the seed
    └── outputs/
        ├── html/                                     ← 5 explorers (balance, hierarchy, map, partition, neighbours)
        └── docs/                                     ← thesis versions, reference PDFs, figures

oil_network_clean/../snapshots/                       ← OUTSIDE the repo (durable on OneDrive)
└── oil_network_stage_1_complete.dump                 ← 1.65 MB pg_dump custom format
```

---

## Common gotchas

- **`pg_restore: error: could not execute query: ERROR: schema "oil_network" already exists`** → re-run with `--clean --if-exists`. The example commands above already do.
- **EIA API returns 403 / 429** (Option B) → your key is missing or rate-limited. Check `.env` is populated and you haven't hit the daily quota.
- **`ModuleNotFoundError: psycopg2`** → the venv isn't activated. Run `python -m venv` step or activate the existing one (`../../.venv/Scripts/Activate.ps1` on Windows PowerShell).
- **Old Postgres version refuses the dump** → `pg_restore` from an older major won't read a PG 18 custom-format dump. Install PG 18, or restore on a machine with PG 18 and pg_dump that DB out into a plain SQL file (`pg_dump -F p`) which is portable across versions.
- **Snapshot file not found** → it lives in `Oil Network Project/snapshots/` on Pedro's OneDrive, *not* in the git repo. Transfer it onto disk first.

---

## How to leave stage 1 (start stage 2)

```bash
git checkout stage_1_complete
git checkout -b stage_2          # or any other branch name
```

From there, anything goes — the tag stays pinned to this commit on `main`, so you can always come back with `git checkout stage_1_complete` no matter what stage 2 turns into.
