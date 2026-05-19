"""Create `oil_network.variable_constraints` — per-variable range constraints.

Schema slot for inequality bounds on variables (e.g. refinery capacity,
pipeline throughput, storage min/max). Sits alongside `variable_assignments`
but with different semantics:

  variable_assignments  -> defines the value of a variable
  variable_constraints  -> bounds the value of a variable

Multiple constraints per variable per scenario are allowed, distinguished by
`kind` (e.g. capacity_physical, capacity_commercial, derating_turnaround).
The PK includes `kind` so multiple coexist; the LP exporter takes the
intersection (effective_max = min over all active maxes).

Temporal semantics match `variable_assignments` and timeseries data: the
active constraint for (variable, scenario, kind) at date d is the row with
the largest `effective_from <= d`. NULL min/max means unbounded in that
direction.

Idempotent: CREATE TABLE IF NOT EXISTS + idempotent index creation.
"""
from __future__ import annotations
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

DDL = """
CREATE TABLE IF NOT EXISTS oil_network.variable_constraints (
    variable_id     TEXT NOT NULL REFERENCES oil_network.variables(variable_id)  ON DELETE CASCADE,
    scenario_id     TEXT NOT NULL REFERENCES oil_network.scenarios(scenario_id)  ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    effective_from  DATE NOT NULL DEFAULT '0001-01-01',
    min_value       DOUBLE PRECISION,
    max_value       DOUBLE PRECISION,
    unit            TEXT,
    source          TEXT,
    notes           TEXT,
    PRIMARY KEY (variable_id, scenario_id, kind, effective_from),
    CHECK (min_value IS NOT NULL OR max_value IS NOT NULL),
    CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
);

CREATE INDEX IF NOT EXISTS ix_var_constraints_active
    ON oil_network.variable_constraints (variable_id, scenario_id, effective_from);
CREATE INDEX IF NOT EXISTS ix_var_constraints_kind
    ON oil_network.variable_constraints (kind, scenario_id);

COMMENT ON TABLE oil_network.variable_constraints IS
'Per-variable inequality bounds (capacity, throughput, storage min/max). '
'NULL min/max means unbounded. Multiple kinds coexist per variable; LP '
'exporter intersects them. Temporal as-of semantics via effective_from. '
'Constraints always apply; the audit_variable_constraints.py post-resolution '
'pass warns when an observed value violates a declared bound (likely TS error).';
"""


def main():
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(DDL)
    print("variable_constraints table created (or already existed)")


if __name__ == "__main__":
    main()
