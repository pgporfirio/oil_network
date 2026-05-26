"""Seed `oil_network.asset_capacities` from `assets.attributes->configuration`.

For every asset with a capacity-like field in its configuration JSONB,
write a `capacity_physical`-source row into asset_capacities. The asset
table's `configuration` becomes the audit trail; the new table is the
queryable source of truth.

  Subtype                       Source field              capacity_kind   Unit   Variable target
  -------                       ------------              -------------   ----   ---------------
  refinery                      capacity_bpd              consumption     kbd    consumption variable
  refinery                      capacity_bd (legacy)      consumption     kbd    consumption variable
  pipeline                      capacity_bpd              throughput      kbd    each directional outflow
  pipeline                      nominal_capacity_bpd      throughput      kbd    each directional outflow
  origin_terminal               storage_capacity_mbbl     storage         mbbl   inventory variable
  storage_terminal              storage_capacity_mbbl     storage         mbbl   inventory variable
  spr_site                      nominal_capacity_mbbl     storage         mbbl   inventory variable
  import_terminal               storage_capacity_mbbl     storage         mbbl   inventory variable
  export_terminal               storage_capacity_mbbl     storage         mbbl   inventory variable
  foreign_production_aggregate  production_capacity_bd    production      kbd    production variable

Conversions: `_bpd` and `_bd` divided by 1000 to land in kbd (flow unit);
`_mbbl` passes through (stock unit).

Idempotent: ON CONFLICT updates max_value in place. To reset, delete
rows with source='asset_configuration_seed' first.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

# (subtype, config_field, capacity_kind, unit, divisor)
MAP = [
    ("refinery",                       "capacity_bpd",             "consumption", "kbd",  1000),
    ("refinery",                       "capacity_bd",              "consumption", "kbd",  1000),  # legacy
    ("origin_terminal",                "storage_capacity_mbbl",    "storage",     "mbbl", 1),
    ("storage_terminal",               "storage_capacity_mbbl",    "storage",     "mbbl", 1),
    ("spr_site",                       "nominal_capacity_mbbl",    "storage",     "mbbl", 1),
    ("import_terminal",                "storage_capacity_mbbl",    "storage",     "mbbl", 1),
    ("export_terminal",                "storage_capacity_mbbl",    "storage",     "mbbl", 1),
    ("foreign_production_aggregate",   "production_capacity_bd",   "production",  "kbd",  1000),
    # US production subtypes — capacities researched 2026-05-15
    ("offshore_region",                "production_capacity_bd",   "production",  "kbd",  1000),
    ("state_conventional",             "production_capacity_bd",   "production",  "kbd",  1000),
    ("state_sub_basin",                "production_capacity_bd",   "production",  "kbd",  1000),
    ("state_residual",                 "production_capacity_bd",   "production",  "kbd",  1000),
    # Pipelines handled separately — capacity applies to both directional outflows
    ("pipeline",                       "capacity_bpd",             "throughput",  "kbd",  1000),
    ("pipeline",                       "nominal_capacity_bpd",     "throughput",  "kbd",  1000),
]

COMMODITY = "crude"  # only commodity in the starter graph


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        print("Seeding asset_capacities from assets.attributes->configuration...")
        total = 0
        for subtype, field, kind, unit, div in MAP:
            cur.execute("""
                INSERT INTO oil_network.asset_capacities
                  (asset_id, commodity, capacity_kind, effective_from,
                   min_value, max_value, unit, source)
                SELECT a.asset_id,
                       %s AS commodity,
                       %s AS capacity_kind,
                       '0001-01-01'::DATE,
                       0,
                       (a.attributes -> 'configuration' ->> %s)::DOUBLE PRECISION / %s,
                       %s,
                       'asset_configuration_seed'
                FROM oil_network.assets a
                WHERE a.node_subtype = %s
                  AND a.attributes -> 'configuration' ? %s
                  AND (a.attributes -> 'configuration' ->> %s)::DOUBLE PRECISION > 0
                ON CONFLICT (asset_id, commodity, capacity_kind, effective_from)
                DO UPDATE SET max_value = EXCLUDED.max_value,
                              unit      = EXCLUDED.unit,
                              source    = EXCLUDED.source
            """, (COMMODITY, kind, field, div, unit, subtype, field, field))
            n = cur.rowcount
            if n > 0:
                print(f"  {subtype:32s} {field:28s} -> {kind:12s} ({unit:4s} /{div}): {n} rows")
            total += n
        # Time-versioned rows from pipeline capacity_history.
        # patch_pipeline_timeline.py writes a JSONB array like:
        #   capacity_history: [{effective_from, capacity_bpd, source, notes}, ...]
        # Each entry produces a row in asset_capacities at that effective_from,
        # so the LP exporter / audit pick the right cap per date via the as-of
        # query in v_effective_constraints + audit_capacity_violations.
        cur.execute("""
            WITH hist AS (
              SELECT a.asset_id,
                     elem->>'effective_from' AS effective_from,
                     (elem->>'capacity_bpd')::DOUBLE PRECISION / 1000.0 AS max_kbd,
                     elem->>'source' AS history_source,
                     elem->>'notes'  AS notes
              FROM oil_network.assets a,
                   jsonb_array_elements(
                       COALESCE(a.attributes->'configuration'->'capacity_history',
                                '[]'::jsonb)) AS elem
              WHERE a.node_subtype = 'pipeline'
            )
            INSERT INTO oil_network.asset_capacities
              (asset_id, commodity, capacity_kind, effective_from,
               min_value, max_value, unit, source, notes)
            SELECT h.asset_id, 'crude', 'throughput',
                   h.effective_from::DATE,
                   0, h.max_kbd, 'kbd',
                   COALESCE(h.history_source, 'asset_configuration_seed'),
                   h.notes
            FROM hist h
            WHERE h.max_kbd IS NOT NULL
              -- Don't shadow the default 0001-01-01 row if the history's earliest
              -- effective_from equals that. Only insert when it's later.
              AND h.effective_from::DATE > '0001-01-01'
            ON CONFLICT (asset_id, commodity, capacity_kind, effective_from)
            DO UPDATE SET max_value = EXCLUDED.max_value,
                          source    = EXCLUDED.source,
                          notes     = EXCLUDED.notes
        """)
        n_hist = cur.rowcount
        if n_hist > 0:
            print(f"  pipeline capacity_history -> throughput (time-versioned): {n_hist} rows")
        total += n_hist

        conn.commit()
        print(f"\nTotal: {total} capacity rows written.")

        # Summary by kind
        print()
        cur.execute("""
            SELECT capacity_kind, count(*), min(max_value), max(max_value), unit
            FROM oil_network.asset_capacities
            GROUP BY capacity_kind, unit
            ORDER BY capacity_kind, unit
        """)
        print(f"{'capacity_kind':14s} {'unit':5s} {'count':>5s} {'min_max':>12s} {'max_max':>12s}")
        for r in cur.fetchall():
            print(f"  {r[0]:12s} {r[4]:5s} {r[1]:>5d} {r[2]:>12.2f} {r[3]:>12.2f}")


if __name__ == "__main__":
    main()
