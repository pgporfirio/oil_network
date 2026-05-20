"""Create oil_network.v_node_routes — all simple directed paths between
physical nodes in the flow graph, up to MAX_HOPS.

Query patterns:
  routes from X       :  WHERE origin = 'x'
  routes to X         :  WHERE destination = 'x'
  routes through X    :  WHERE 'x' = ANY(path)

Refresh: structural — rebuild when v_flow_edges changes.
"""
from __future__ import annotations
import time
import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
MAX_HOPS = 12

DDL = f"""
DROP MATERIALIZED VIEW IF EXISTS oil_network.v_node_routes CASCADE;

CREATE MATERIALIZED VIEW oil_network.v_node_routes AS
WITH RECURSIVE
  edges AS (
    SELECT DISTINCT e.source, e.target
    FROM oil_network.v_flow_edges e
    JOIN oil_network.assets s ON s.asset_id = e.source AND s.kind = 'physical'
    JOIN oil_network.assets t ON t.asset_id = e.target AND t.kind = 'physical'
  ),
  routes(origin, destination, path, hops) AS (
    SELECT source, target, ARRAY[source, target], 1 FROM edges
    UNION ALL
    SELECT r.origin, e.target, r.path || e.target, r.hops + 1
    FROM routes r JOIN edges e ON e.source = r.destination
    WHERE NOT (e.target = ANY(r.path)) AND r.hops < {MAX_HOPS}
  )
SELECT ROW_NUMBER() OVER (ORDER BY origin, destination, hops) AS route_id,
       origin, destination, hops, path
FROM routes;

CREATE UNIQUE INDEX IF NOT EXISTS ix_routes_pk   ON oil_network.v_node_routes (route_id);
CREATE INDEX IF NOT EXISTS ix_routes_origin      ON oil_network.v_node_routes (origin);
CREATE INDEX IF NOT EXISTS ix_routes_destination ON oil_network.v_node_routes (destination);
CREATE INDEX IF NOT EXISTS ix_routes_path_gin    ON oil_network.v_node_routes USING GIN (path);
"""


def main() -> None:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        t0 = time.time()
        cur.execute(DDL); conn.commit()
        cur.execute("SELECT COUNT(*), MAX(hops), AVG(hops)::numeric(4,2) FROM oil_network.v_node_routes")
        n, mx, avg = cur.fetchone()
        print(f"v_node_routes: {n:,} routes, avg {avg} hops, max {mx} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
