"""One-shot consistency check after a clean build. Throwaway diagnostic."""
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
EXP = dict(nodes=252, variables=1870, var_assignments_min=900,
           defaults=76, ts_data_min=17000, resolved_min=225_000, capacities=215)

with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
    def s(sql, *args):
        cur.execute(sql, args); return cur.fetchone()[0]

    print("=" * 78); print("1. HEADLINE COUNTS"); print("=" * 78)
    n_assets = s("SELECT COUNT(*) FROM oil_network.assets")
    cur.execute("""SELECT node_type, COUNT(*) FROM oil_network.nodes GROUP BY node_type ORDER BY COUNT(*) DESC""")
    by_type = cur.fetchall()
    print(f"  assets:                       {n_assets}   expected {EXP['nodes']}")
    print(f"  nodes:                        {s('SELECT COUNT(*) FROM oil_network.nodes')}")
    print(f"    node_type breakdown: " + ", ".join(f"{t}={n}" for t, n in by_type[:6]))
    print(f"                          " + ", ".join(f"{t}={n}" for t, n in by_type[6:12]))
    print(f"                          " + ", ".join(f"{t}={n}" for t, n in by_type[12:]))
    print(f"  variables:                    {s('SELECT COUNT(*) FROM oil_network.variables')}   expected ~{EXP['variables']}")
    print(f"  variable_assignments:         {s('SELECT COUNT(*) FROM oil_network.variable_assignments')}   expected >= {EXP['var_assignments_min']}")
    print(f"  node_type_default_formulas:   {s('SELECT COUNT(*) FROM oil_network.node_type_default_formulas')}   expected {EXP['defaults']}")
    print(f"  scenarios:                    {s('SELECT COUNT(*) FROM oil_network.scenarios')}")
    print(f"  timeseries (catalogue):       {s('SELECT COUNT(*) FROM oil_network.timeseries')}")
    print(f"  timeseries_data:              {s('SELECT COUNT(*) FROM oil_network.timeseries_data')}   expected >= {EXP['ts_data_min']}")
    print(f"  scenario_resolved_values:     {s('SELECT COUNT(*) FROM oil_network.scenario_resolved_values')}   expected >= {EXP['resolved_min']}")
    print(f"  asset_capacities:             {s('SELECT COUNT(*) FROM oil_network.asset_capacities')}   expected ~{EXP['capacities']}")
    print(f"  variable_constraints:         {s('SELECT COUNT(*) FROM oil_network.variable_constraints')}   expected 0")

    print()
    print("=" * 78); print("2. RESOLVER DISPATCH"); print("=" * 78)
    cur.execute("""SELECT source, COUNT(*) FROM oil_network.scenario_resolved_values
                   GROUP BY source ORDER BY COUNT(*) DESC""")
    for src, n in cur.fetchall():
        print(f"  {src or '(null)':<24} {n:>10,}")

    print()
    print("=" * 78); print("3. AUDITS"); print("=" * 78)
    print(f"  unresolved rows:              {s('SELECT COUNT(*) FROM oil_network.scenario_resolved_values WHERE source=%s', 'unresolved')}   (must be 0)")

    cur.execute("""SELECT timeseries_id, COUNT(*) FROM oil_network.variable_assignments
                   WHERE timeseries_id IS NOT NULL
                   GROUP BY timeseries_id HAVING COUNT(*) > 1""")
    print(f"  TS-binding collisions:        {len(cur.fetchall())}   (must be 0)")

    cur.execute("""SELECT status, COUNT(*) FROM oil_network.v_aggregation_consistency
                   WHERE observation_date='2024-12-01' GROUP BY status ORDER BY 2 DESC""")
    print(f"  v_aggregation_consistency @ 2024-12-01:")
    for st, n in cur.fetchall(): print(f"    {st:<22} {n:>6}")

    cur.execute("""SELECT status, COUNT(*) FROM oil_network.v_aggregation_consistency
                   GROUP BY status ORDER BY 2 DESC""")
    print(f"  v_aggregation_consistency total:")
    for st, n in cur.fetchall(): print(f"    {st:<22} {n:>6}")

    print()
    print("=" * 78); print("4. PARTITION CLOSURE @ 2024-12-01 (USA + 5 PADD views)"); print("=" * 78)
    cur.execute("""SELECT parent_node_id, variable_type, parent_value, sum_children, gap_kbd,
                          n_latent, n_children_declared, status
                   FROM oil_network.v_aggregation_consistency
                   WHERE observation_date='2024-12-01'
                     AND parent_node_id IN ('usa_view','padd1_view','padd2_view','padd3_view','padd4_view','padd5_view')
                   ORDER BY parent_node_id, variable_type""")
    rows = cur.fetchall()
    print(f"  {'parent':<14} {'vtype':<16} {'observed':>10} {'sum':>10} {'gap':>9}  miss/decl  status")
    n_red = 0
    for r in rows:
        obs = f"{r[2]:>10.0f}" if r[2] is not None else "      n/a "
        ss  = f"{r[3]:>10.0f}" if r[3] is not None else "      n/a "
        gap = f"{r[4]:>9.1f}" if r[4] is not None else "      n/a"
        flag = ""
        if r[4] is not None and abs(r[4]) > max(1, 0.01 * (r[2] or 0)) and r[5] == 0:
            flag = "  <-- RED (children full, gap > tol)"
            n_red += 1
        print(f"  {r[0]:<14} {r[1]:<16} {obs} {ss} {gap}  {r[5]}/{r[6]}     {r[7]}{flag}")
    print(f"\n  Red cells at USA/PADD level: {n_red}  (per PROJECT_STATE: documented gaps)")

    print()
    print("=" * 78); print("5. SPOT CHECKS @ 2024-12-01"); print("=" * 78)
    def val(node, vtype):
        cur.execute("""SELECT srv.value
                       FROM oil_network.scenario_resolved_values srv
                       JOIN oil_network.variables v ON v.variable_id = srv.variable_id
                       WHERE v.node_id=%s AND v.variable_type=%s AND v.commodity='crude'
                         AND v.related_node_id IS NULL
                         AND srv.observation_date='2024-12-01' AND srv.value IS NOT NULL
                       LIMIT 1""", (node, vtype))
        r = cur.fetchone(); return r[0] if r else None
    checks = [
        ("USA production (kbd)",   "usa_view",   "production",   13_000, 13_500),
        ("USA consumption (kbd)",  "usa_view",   "consumption",  15_800, 16_500),
        ("USA outflow (kbd)",      "usa_view",   "outflow",       3_500,  4_500),
        ("USA inflow (kbd)",       "usa_view",   "inflow",        6_000,  7_000),
        ("PADD3 production (kbd)", "padd3_view", "production",    9_500, 10_500),
        ("PADD3 consumption (kbd)","padd3_view", "consumption",   8_800,  9_500),
        ("Permian-TX prod (kbd)",  "permian_tx", "production",    5_500,  6_300),
        ("Bakken-ND prod (kbd)",   "bakken_nd",  "production",      900,  1_300),
        ("SPR inv (mbbl)",         "spr_total",  "inventory",   380_000,400_000),
        ("USA inv (mbbl)",         "usa_view",   "inventory",   790_000,900_000),
    ]
    print(f"  {'variable':<26}{'value':>14}  range")
    bad = 0
    for label, node, vt, lo, hi in checks:
        v = val(node, vt)
        if v is None:
            print(f"  {label:<26}{'MISSING':>14}"); bad += 1; continue
        if lo <= v <= hi:
            mark = "OK "
        else:
            mark = "BAD"; bad += 1
        print(f"  {label:<26}{v:>14,.0f}  {mark} [{lo:,}, {hi:,}]")
    print(f"\n  Out-of-range: {bad}")

    print()
    print("=" * 78); print("6. RESOLUTION ANOMALIES (v_resolution_anomalies)"); print("=" * 78)
    # Query the general post-build audit view. Replaces the previous ad-hoc
    # negative-value check; see claude/DESIGN_PRINCIPLES.md Corollary D-bis.
    cur.execute("""SELECT severity, anomaly_kind, COUNT(*)
                   FROM oil_network.v_resolution_anomalies
                   GROUP BY severity, anomaly_kind
                   ORDER BY severity DESC, anomaly_kind""")
    rows = cur.fetchall()
    if not rows:
        print("  Clean — no anomalies of any kind  OK")
    else:
        n_high = 0
        print(f"    {'severity':<10}{'anomaly_kind':<24}{'count':>8}")
        for sev, kind, n in rows:
            print(f"    {sev:<10}{kind:<24}{n:>8}")
            if sev == "high":
                n_high += n
        if n_high:
            print(f"  -- {n_high} high-severity row(s)  INVESTIGATE")
        else:
            print("  -- All low-severity (forecast-horizon LOCF artefacts, expected)")

    cur.execute("""SELECT COUNT(*), MIN(srv.value), MAX(srv.value)
                   FROM oil_network.scenario_resolved_values srv
                   JOIN oil_network.variables v ON v.variable_id = srv.variable_id
                   WHERE v.variable_type='balancing_item' AND srv.value IS NOT NULL
                     AND srv.observation_date='2024-12-01'""")
    r = cur.fetchone()
    print(f"  balancing_item @ 2024-12-01: n={r[0]}, range [{r[1]:.0f}, {r[2]:.0f}]  (signed is fine)")

    print()
    print("=" * 78); print("7. CAPACITY VIOLATIONS @ 2024-12-01"); print("=" * 78)
    cur.execute("""SELECT COUNT(*) FROM oil_network.scenario_resolved_values srv
                   JOIN oil_network.v_effective_constraints ec
                     ON srv.variable_id = ec.variable_id
                   WHERE srv.observation_date='2024-12-01'
                     AND srv.value IS NOT NULL
                     AND ec.max_value IS NOT NULL
                     AND srv.value > ec.max_value * 1.001""")
    print(f"  capacity violations @ 2024-12-01: {cur.fetchone()[0]}   (target 0)")

print()
print("Verification complete.")
