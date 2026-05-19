"""NetworkGraph — in-memory representation of an oil_network scenario.

Single source of truth for every renderer / analytic / future consumer.
Loads exclusively from the layer-3 / layer-4 materialised views and the
`scenario_resolved_values` table — never reads `variable_assignments`
directly, never duplicates the partition-tree query.

Usage:
    from network_graph import NetworkGraph
    g = NetworkGraph("starter_us_crude_2015_2025")
    g.partition_children("padd2_view")    # ['bakken_nd', 'oklahoma', ...]
    g.status("permian_tx")                # 'collapsed' / 'authoritative' / 'derived'
    g.value(var_id, observation_date)
    g.node_balance("padd2_view", "2024-12-01")

Eager loading by default: all dates + all variables are read at
construction. For the starter scenario this is roughly 220k rows and
loads in well under a second. To restrict to a single date, pass
`observation_date=...` and only that slice will be loaded.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as _date
from functools import lru_cache
from typing import Iterable, Optional

import psycopg2

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")


# ---------------------------------------------------------------------------
# Dataclasses for clarity (zero-cost; just structured records)
# ---------------------------------------------------------------------------

@dataclass
class Node:
    asset_id: str
    name: str
    kind: str             # 'physical' or 'abstract'
    node_class: Optional[str]
    node_subtype: Optional[str]
    description: Optional[str]
    # Geographic / contextual metadata (may be None for nodes without locations)
    lat: Optional[float] = None
    lon: Optional[float] = None
    state: Optional[str] = None
    padd: Optional[str] = None
    country: Optional[str] = None
    # Per-scenario derived
    status: Optional[str] = None     # 'authoritative' / 'derived' / 'collapsed'


@dataclass
class FlowEdge:
    source: str
    target: str
    commodity: str
    via_variable: str


# ---------------------------------------------------------------------------
# NetworkGraph
# ---------------------------------------------------------------------------

class NetworkGraph:
    """The compiled graph for one scenario.

    Constructed once per session. Methods provide cheap navigation,
    value lookup, and aggregated balance computation. Renderers consume
    this object instead of issuing their own SQL.
    """

    def __init__(self, scenario_id: str,
                 observation_date: Optional[str] = None,
                 db_config: dict = None):
        self.scenario_id = scenario_id
        self._restrict_date = observation_date
        self._db = db_config or DB

        # In-memory structures (built by _load)
        self.nodes: dict[str, Node] = {}
        self.flow_edges: list[FlowEdge] = []
        self._by_source: dict[str, list[FlowEdge]] = defaultdict(list)
        self._by_target: dict[str, list[FlowEdge]] = defaultdict(list)

        # Partition tree: parent -> [children]   (collapsed across variable_types)
        self._partition_children: dict[str, set[str]] = defaultdict(set)
        self._partition_parents:  dict[str, set[str]] = defaultdict(set)
        # Also keep type-aware: (parent, variable_type) -> [children]
        self._partition_children_typed: dict[tuple[str, str], set[str]] = defaultdict(set)

        # Resolved values: variable_id -> date -> value
        self._values: dict[str, dict[_date, float]] = defaultdict(dict)
        # Variable -> (node_id, variable_type, related_node_id) for fast lookup
        self._var_meta: dict[str, tuple[str, str, Optional[str]]] = {}

        # Status: scenario_id is fixed, so just node_id -> status
        self._status: dict[str, str] = {}

        # Scenario node roles ('balance' / 'constraint' / etc.) from
        # oil_network.scenario_node_role, indexed by node_id under this scenario.
        self._node_roles: dict[str, str] = {}

        # Per-node per-date balance aggregates from v_node_pcisob (L4e mat view).
        # Keyed (node_id, date) -> {P, C, I, O, B, S, dS} (omitting None values).
        self._pcisob: dict[tuple[str, _date], dict[str, float]] = {}

        # Pre-indexed (node_id, variable_type) -> list of variable_ids.
        # Built at the end of _load(); makes `variables_on_node` and
        # `per_edge_values` O(1) instead of scanning _var_meta.
        self._vars_by_node_type: dict[tuple[str, str], list[str]] = defaultdict(list)

        # Variable id -> assignment recipe (timeseries_id, formula, formula_inputs).
        # Lets consumers know whether a variable is TS-bound or formula-bound
        # without re-querying.
        self._var_assignment: dict[str, tuple[Optional[str], Optional[str], list[str]]] = {}

        # Date coverage
        self.dates: list[_date] = []

        self._load()

    # -----------------------------------------------------------------------
    # Loading
    # -----------------------------------------------------------------------

    def _load(self):
        with psycopg2.connect(**self._db) as conn, conn.cursor() as cur:
            # 1. Nodes
            cur.execute("""
                SELECT a.asset_id, a.name, a.kind, a.node_class, a.node_subtype,
                       a.description,
                       l.lat, l.lon, l.state, l.padd, l.country
                FROM oil_network.assets a
                LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            """)
            for r in cur.fetchall():
                n = Node(
                    asset_id=r[0], name=r[1] or r[0], kind=r[2],
                    node_class=r[3], node_subtype=r[4], description=r[5],
                    lat=float(r[6]) if r[6] is not None else None,
                    lon=float(r[7]) if r[7] is not None else None,
                    state=r[8], padd=r[9], country=r[10],
                )
                self.nodes[r[0]] = n

            # 2. Flow edges
            cur.execute("""
                SELECT source, target, commodity, via_variable
                FROM oil_network.v_flow_edges
            """)
            for src, tgt, com, via in cur.fetchall():
                e = FlowEdge(source=src, target=tgt, commodity=com, via_variable=via)
                self.flow_edges.append(e)
                self._by_source[src].append(e)
                self._by_target[tgt].append(e)

            # 3. Partition tree (L3a)
            cur.execute("""
                SELECT parent_node_id, child_node_id, variable_type
                FROM oil_network.v_partition_tree
                WHERE scenario_id = %s
            """, (self.scenario_id,))
            for p, c, vt in cur.fetchall():
                self._partition_children[p].add(c)
                self._partition_parents[c].add(p)
                self._partition_children_typed[(p, vt)].add(c)

            # 4. Node status (L3b)
            cur.execute("""
                SELECT node_id, status FROM oil_network.v_node_status
                WHERE scenario_id = %s
            """, (self.scenario_id,))
            for nid, status in cur.fetchall():
                self._status[nid] = status
                if nid in self.nodes:
                    self.nodes[nid].status = status

            # 5. Variable metadata
            cur.execute("""
                SELECT variable_id, node_id, variable_type, related_node_id
                FROM oil_network.variables
            """)
            for vid, nid, vt, rel in cur.fetchall():
                self._var_meta[vid] = (nid, vt, rel)

            # 5b. Per-variable assignment recipe (from v_effective_assignments)
            cur.execute("""
                SELECT variable_id, timeseries_id, formula, formula_inputs
                FROM oil_network.v_effective_assignments
                WHERE scenario_id = %s
            """, (self.scenario_id,))
            for vid, ts, formula, finputs in cur.fetchall():
                self._var_assignment[vid] = (ts, formula, list(finputs or []))

            # 5c. Scenario node roles (balance / constraint)
            cur.execute("""
                SELECT node_id, role FROM oil_network.scenario_node_role
                WHERE scenario_id = %s
            """, (self.scenario_id,))
            for nid, role in cur.fetchall():
                self._node_roles[nid] = role

            # 5d. Per-node per-date balance aggregates (L4e materialised view).
            # Pre-computed in SQL so we don't iterate variables-on-node at
            # render time. One row per (node, date) with full P/C/I/O/B/S/dS.
            if self._restrict_date:
                cur.execute("""
                    SELECT node_id, observation_date, p, c, i, o, b, s_mbbl, ds_kbd
                    FROM oil_network.v_node_pcisob
                    WHERE scenario_id = %s AND observation_date = %s
                """, (self.scenario_id, self._restrict_date))
            else:
                cur.execute("""
                    SELECT node_id, observation_date, p, c, i, o, b, s_mbbl, ds_kbd
                    FROM oil_network.v_node_pcisob
                    WHERE scenario_id = %s
                """, (self.scenario_id,))
            for nid, d, p, c, i, o, b, s, ds in cur.fetchall():
                slot = {}
                if p  is not None: slot["P"]  = float(p)
                if c  is not None: slot["C"]  = float(c)
                if i  is not None: slot["I"]  = float(i)
                if o  is not None: slot["O"]  = float(o)
                if b  is not None: slot["B"]  = float(b)
                if s  is not None: slot["S"]  = float(s)
                if ds is not None: slot["dS"] = float(ds)
                if slot:
                    self._pcisob[(nid, d)] = slot

            # 6. Resolved values
            if self._restrict_date:
                cur.execute("""
                    SELECT variable_id, observation_date, value
                    FROM oil_network.scenario_resolved_values
                    WHERE scenario_id = %s AND observation_date = %s
                """, (self.scenario_id, self._restrict_date))
            else:
                cur.execute("""
                    SELECT variable_id, observation_date, value
                    FROM oil_network.scenario_resolved_values
                    WHERE scenario_id = %s
                """, (self.scenario_id,))
            dates_seen = set()
            for vid, d, val in cur.fetchall():
                if val is not None:
                    self._values[vid][d] = float(val)
                dates_seen.add(d)
            self.dates = sorted(dates_seen)

        # Build the per-node, per-variable-type index so
        # variables_on_node() and per_edge_values() are O(1) lookups.
        for vid, (nid, vt, _rel) in self._var_meta.items():
            self._vars_by_node_type[(nid, vt)].append(vid)

        # Coordinate inference for nodes lacking native lat/lon (pipelines etc.).
        # Use flow-edge neighbours' coords, iterate up to 4 passes.
        self._infer_coords()

    def _infer_coords(self):
        neighbours: dict[str, set[str]] = defaultdict(set)
        for e in self.flow_edges:
            neighbours[e.source].add(e.target)
            neighbours[e.target].add(e.source)
        for _ in range(4):
            changed = False
            for nid, node in self.nodes.items():
                if node.lat is not None:
                    continue
                ncoords = [(self.nodes[nb].lat, self.nodes[nb].lon)
                           for nb in neighbours.get(nid, ())
                           if nb in self.nodes and self.nodes[nb].lat is not None]
                if ncoords:
                    node.lat = sum(c[0] for c in ncoords) / len(ncoords)
                    node.lon = sum(c[1] for c in ncoords) / len(ncoords)
                    changed = True
            if not changed:
                break

    # -----------------------------------------------------------------------
    # Hierarchy navigation
    # -----------------------------------------------------------------------

    def partition_children(self, node_id: str,
                            variable_type: Optional[str] = None) -> list[str]:
        """Return the partition children of node_id.

        If variable_type is given, restrict to children for that type only.
        Otherwise the union across all types.
        """
        if variable_type is not None:
            return sorted(self._partition_children_typed.get((node_id, variable_type), ()))
        return sorted(self._partition_children.get(node_id, ()))

    def partition_parents(self, node_id: str) -> list[str]:
        return sorted(self._partition_parents.get(node_id, ()))

    @lru_cache(maxsize=None)
    def descendants(self, node_id: str) -> frozenset[str]:
        """All transitive descendants of node_id (including node_id itself)."""
        out = {node_id}
        stack = [node_id]
        while stack:
            n = stack.pop()
            for c in self._partition_children.get(n, ()):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return frozenset(out)

    def roots(self) -> list[str]:
        """Partition-tree roots: nodes with no partition parent."""
        return sorted(n for n in self.nodes if n not in self._partition_parents)

    def leaves(self) -> list[str]:
        """Partition-tree leaves: nodes with no partition children."""
        return sorted(n for n in self.nodes if n not in self._partition_children)

    # -----------------------------------------------------------------------
    # Flow edges
    # -----------------------------------------------------------------------

    def outflows_from(self, node_id: str) -> list[FlowEdge]:
        return list(self._by_source.get(node_id, ()))

    def inflows_to(self, node_id: str) -> list[FlowEdge]:
        return list(self._by_target.get(node_id, ()))

    # -----------------------------------------------------------------------
    # Status & metadata
    # -----------------------------------------------------------------------

    def status(self, node_id: str) -> str:
        return self._status.get(node_id, "collapsed")

    def coords(self, node_id: str) -> Optional[tuple[float, float]]:
        n = self.nodes.get(node_id)
        if not n or n.lat is None:
            return None
        return (n.lat, n.lon)

    def role(self, node_id: str) -> Optional[str]:
        """Scenario role of a node: 'balance' / 'constraint' / 'observation' / None."""
        return self._node_roles.get(node_id)

    # -----------------------------------------------------------------------
    # Variable accessors
    # -----------------------------------------------------------------------

    def variables(self) -> list[dict]:
        """All variable definitions as dicts {variable_id, node_id, variable_type, related_node_id}."""
        return [
            {"variable_id": vid, "node_id": meta[0],
             "variable_type": meta[1], "related_node_id": meta[2]}
            for vid, meta in self._var_meta.items()
        ]

    def variables_on_node(self, node_id: str,
                          variable_type: Optional[str] = None) -> list[str]:
        """Every variable_id on node_id (optionally filtered by variable_type).

        O(1) via the pre-built `_vars_by_node_type` index.
        """
        if variable_type is not None:
            return list(self._vars_by_node_type.get((node_id, variable_type), ()))
        # All variable types: concatenate per-type lists
        out: list[str] = []
        for (nid, _vt), vids in self._vars_by_node_type.items():
            if nid == node_id:
                out.extend(vids)
        return out

    def assignment(self, variable_id: str) -> Optional[tuple[Optional[str], Optional[str], list[str]]]:
        """Return (timeseries_id, formula, formula_inputs) under this scenario, or None."""
        return self._var_assignment.get(variable_id)

    # -----------------------------------------------------------------------
    # Values
    # -----------------------------------------------------------------------

    def value(self, variable_id: str, observation_date) -> Optional[float]:
        d = observation_date if isinstance(observation_date, _date) \
            else _date.fromisoformat(observation_date)
        return self._values.get(variable_id, {}).get(d)

    def node_variable_id(self, node_id: str, variable_type: str,
                          related_node_id: Optional[str] = None) -> Optional[str]:
        """Compose the variable_id following the project convention."""
        if related_node_id is None:
            return f"{variable_type}__crude__{node_id}"
        return f"{variable_type}__crude__{node_id}__{related_node_id}"

    def inventory_delta(self, node_id: str, observation_date) -> Optional[float]:
        """ΔS in kbd for node_id at observation_date.

        Looks up the inventory variable's current value vs the previous month's
        value (from the loaded `dates` list). Returns the delta normalised by
        days-in-month so consumers can compare against P/C/I/O which are all
        kbd. Returns None if either S(t) or S(t-1) is missing.
        """
        d = observation_date if isinstance(observation_date, _date) \
            else _date.fromisoformat(observation_date)
        s_var = self.node_variable_id(node_id, "inventory")
        s_now = self._values.get(s_var, {}).get(d)
        if s_now is None:
            return None
        # find previous date in our sorted list
        if d not in self.dates:
            return None
        idx = self.dates.index(d)
        if idx == 0:
            return None
        d_prev = self.dates[idx - 1]
        s_prev = self._values.get(s_var, {}).get(d_prev)
        if s_prev is None:
            return None
        next_m = (d.month % 12) + 1
        next_y = d.year + (1 if d.month == 12 else 0)
        days = (_date(next_y, next_m, 1) - _date(d.year, d.month, 1)).days
        return (s_now - s_prev) / days

    def pcisob(self, node_id: str, observation_date) -> dict:
        """Per-letter aggregate for the balance equation at one date.

        Reads from the L4e `v_node_pcisob` materialised view loaded at
        construction. Returns a dict with keys 'P', 'C', 'I', 'O', 'B', 'S',
        'dS' (kbd except S which is mbbl level). Letters with no resolved
        value are absent. O(1) lookup — no per-variable iteration.
        """
        d = observation_date if isinstance(observation_date, _date) \
            else _date.fromisoformat(observation_date)
        return self._pcisob.get((node_id, d), {})

    def per_edge_values(self, node_id: str, variable_type: str,
                         observation_date) -> list[dict]:
        """Inflow or outflow edge breakdown for one node at one date.

        Returns list of {"r": related_node, "v": value} for every relational
        variable of the given type at this node whose resolved value is not
        None. Used by the balance HTML to filter intra-partition flows.
        """
        d = observation_date if isinstance(observation_date, _date) \
            else _date.fromisoformat(observation_date)
        out = []
        for vid in self.variables_on_node(node_id, variable_type):
            meta = self._var_meta[vid]
            rel = meta[2]
            if rel is None:
                continue
            v = self._values.get(vid, {}).get(d)
            if v is None:
                continue
            out.append({"r": rel, "v": v})
        return out

    def node_balance(self, node_id: str, observation_date) -> dict:
        """Return P, C, S, B, sum_in, sum_out for node_id at observation_date.

        All values in kbd (sum_in/sum_out are per-day rates summed across
        all inflow/outflow variables on this node). S is mbbl (inventory
        level); the inventory delta is left to v_inventory_changes.
        """
        d = observation_date if isinstance(observation_date, _date) \
            else _date.fromisoformat(observation_date)

        def get_scalar(vt: str) -> Optional[float]:
            vid = self.node_variable_id(node_id, vt)
            return self._values.get(vid, {}).get(d)

        # Inflow / outflow are relational — iterate via flow edges
        sum_in  = 0.0
        sum_out = 0.0
        n_in = n_out = 0
        for e in self._by_target.get(node_id, ()):
            v = self._values.get(
                f"inflow__crude__{node_id}__{e.source}", {}).get(d)
            if v is not None:
                sum_in += v
                n_in += 1
        for e in self._by_source.get(node_id, ()):
            v = self._values.get(
                f"outflow__crude__{node_id}__{e.target}", {}).get(d)
            if v is not None:
                sum_out += v
                n_out += 1

        return {
            "node_id": node_id,
            "date": d,
            "P":   get_scalar("production"),
            "C":   get_scalar("consumption"),
            "S":   get_scalar("inventory"),
            "B":   get_scalar("balancing_item"),
            "sum_in":  sum_in  if n_in  else None,
            "sum_out": sum_out if n_out else None,
            "n_inflow_edges":  n_in,
            "n_outflow_edges": n_out,
        }

    # -----------------------------------------------------------------------
    # Filtering
    # -----------------------------------------------------------------------

    def by_subtype(self, subtype: str) -> list[str]:
        return sorted(n.asset_id for n in self.nodes.values()
                      if n.node_subtype == subtype)

    def by_padd(self, padd: str) -> list[str]:
        return sorted(n.asset_id for n in self.nodes.values()
                      if (n.padd or "").endswith(str(padd)))

    def by_kind(self, kind: str) -> list[str]:
        return sorted(n.asset_id for n in self.nodes.values() if n.kind == kind)

    # -----------------------------------------------------------------------
    # Misc
    # -----------------------------------------------------------------------

    def __repr__(self):
        return (f"NetworkGraph(scenario={self.scenario_id!r}, "
                f"nodes={len(self.nodes)}, flow_edges={len(self.flow_edges)}, "
                f"partition_edges={sum(len(c) for c in self._partition_children.values())}, "
                f"values={sum(len(v) for v in self._values.values())}, "
                f"dates={len(self.dates)})")


# ---------------------------------------------------------------------------
# CLI sanity check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    g = NetworkGraph("starter_us_crude_2015_2025")
    dt = (time.perf_counter() - t0) * 1000
    print(f"Loaded in {dt:.0f} ms")
    print(g)
    print()
    print("=== padd2_view partition children ===")
    for c in g.partition_children("padd2_view"):
        st = g.status(c)
        print(f"  {c:35s} [{st}]")
    print()
    print("=== padd2_view balance at 2024-12-01 ===")
    bal = g.node_balance("padd2_view", "2024-12-01")
    for k, v in bal.items():
        if isinstance(v, float):
            print(f"  {k:18s} {v:>10.1f}")
        else:
            print(f"  {k:18s} {v}")
