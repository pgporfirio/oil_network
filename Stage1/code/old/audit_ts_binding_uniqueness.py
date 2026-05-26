"""Audit: a single TS bound as authoritative on more than one variable in the
same scenario violates Principle 2.8 (observed_authoritative is exclusive).

A TS represents one physical observation. Binding it to two variables means
the same number enters the mass balance twice — silent double-counting.

The check:
  For each (scenario_id, timeseries_id) in variable_assignments where
  timeseries_id IS NOT NULL, count the number of distinct variable_ids.
  Any count > 1 is a violation.

Auxiliary observations (Principle 2.8: "additional observed series may be
attached as observed_auxiliary") would normally be tagged with a different
status in a richer schema. In this schema we do not distinguish auxiliary
from authoritative at the variable level — the equivalent is to bind the
auxiliary TS to a node that is OUTSIDE the balance partition (role=constraint
in scenario_node_role). The audit therefore also reports, for each
multi-binding, the roles of the bound nodes to help judge whether the binding
is intentional (one authoritative + one auxiliary) or a true violation.

Reads from variable_assignments directly (the override layer), since
v_effective_assignments inherits from node_type_default_formulas which are
formula-based, not TS-based, so cannot generate this kind of collision.
"""

from __future__ import annotations

import sys
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


def main(scenario: str | None = None) -> int:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        if scenario:
            scope = "WHERE scenario_id = %s"
            params: tuple = (scenario,)
        else:
            scope = ""
            params = ()

        # 1. Find duplicates.
        cur.execute(
            f"""
            WITH bindings AS (
                SELECT scenario_id, timeseries_id, variable_id
                FROM oil_network.variable_assignments
                {scope}{' AND' if scope else 'WHERE'} timeseries_id IS NOT NULL
            )
            SELECT scenario_id, timeseries_id,
                   COUNT(*) AS n_variables,
                   ARRAY_AGG(variable_id ORDER BY variable_id) AS variables
            FROM bindings
            GROUP BY scenario_id, timeseries_id
            HAVING COUNT(*) > 1
            ORDER BY scenario_id, timeseries_id
            """,
            params,
        )
        dupes = cur.fetchall()

        # 2. For each duplicate, enrich with node + role info.
        if dupes:
            print(f"=== {len(dupes)} TS-binding collision(s) found ===\n")
            for sid, tsid, n, variables in dupes:
                cur.execute(
                    """
                    SELECT v.variable_id, v.node_id, v.variable_type,
                           v.related_node_id, r.role
                    FROM oil_network.variables v
                    LEFT JOIN oil_network.scenario_node_role r
                         ON r.node_id = v.node_id AND r.scenario_id = %s
                    WHERE v.variable_id = ANY(%s)
                    ORDER BY v.variable_id
                    """,
                    (sid, list(variables)),
                )
                rows = cur.fetchall()
                # Pull the TS name for context.
                cur.execute(
                    "SELECT name, unit FROM oil_network.timeseries WHERE timeseries_id = %s",
                    (tsid,),
                )
                ts_row = cur.fetchone()
                ts_label = f"{ts_row[0]} [{ts_row[1]}]" if ts_row else "(unknown)"

                print(f"  scenario   : {sid}")
                print(f"  timeseries : {tsid}")
                print(f"               {ts_label}")
                print(f"  n_variables: {n}")
                roles = set()
                for vid, nid, vtype, rel, role in rows:
                    rel_s = f"-> {rel}" if rel else ""
                    role_s = role or "(unscoped)"
                    roles.add(role_s)
                    print(f"    [{role_s:10}] {vtype:7}  {nid:24} {rel_s:30}  ({vid})")

                # Categorise
                if len(rows) == 2 and {"balance", "constraint"} <= roles:
                    verdict = "INTENTIONAL: one authoritative (balance) + one auxiliary (constraint)"
                elif "balance" in roles and len([r for _,_,_,_,r in rows if r == "balance"]) > 1:
                    verdict = "VIOLATION: multiple balance-role variables share a TS"
                elif len(roles) == 1 and "(unscoped)" in roles:
                    verdict = "REVIEW: all variables unscoped — cannot determine if intentional"
                else:
                    verdict = "REVIEW: check role mix manually"
                print(f"  verdict    : {verdict}\n")
        else:
            print("=== 0 TS-binding collisions ===")
            print("No timeseries is bound as authoritative to more than one variable in")
            print("the same scenario. Principle 2.8 holds on TS attribution.")

        # 3. Summary stats
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT timeseries_id), COUNT(*)
            FROM oil_network.variable_assignments
            {scope}{' AND' if scope else 'WHERE'} timeseries_id IS NOT NULL
            """,
            params,
        )
        n_distinct_ts, n_bindings = cur.fetchone()
        scope_label = f"scenario={scenario}" if scenario else "all scenarios"
        print(f"\n[summary {scope_label}] {n_bindings} TS-bound variables / {n_distinct_ts} distinct TS used")

    return len(dupes)


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else None
    n = main(scenario)
    sys.exit(1 if n > 0 else 0)
