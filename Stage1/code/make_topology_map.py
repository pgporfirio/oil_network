"""Thesis-figure renderer: physical nodes + PADD boundaries, no edges.

Replaces Figure 3.1 in the restructured thesis (v2.0). Designed to be
print-friendly: single muted node colour, shape by node_subtype, PADD regions
shaded as a soft choropleth, legend only (no per-node labels).

Outputs three files in outputs/html/:
  - fig_topology_padd.html  interactive Plotly (inspection)
  - fig_topology_padd.svg   static vector (drop-in for thesis docx)
  - fig_topology_padd.png   static raster (Word fallback)

Data source: oil_network.assets (kind='physical') joined to locations for
lat/lon and PADD. Pipelines lack stored coords and are inferred by averaging
their connected physical neighbours (same logic as make_map.py).

Nodes shown: every US physical node that resolves to a (lat, lon) and carries
a PADD assignment. Drops:
  - Canadian foreign_production_aggregate (outside Albers-USA viewport)
  - foreign_export_destination (no coords)
  - state_residual (abstract PADD-residuals, no specific location)
"""
from __future__ import annotations
from paths import HTML_DIR

from collections import defaultdict
from pathlib import Path

import psycopg2
import plotly.graph_objects as go

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")

OUT_HTML = HTML_DIR / "fig_topology_padd.html"
OUT_SVG  = HTML_DIR / "fig_topology_padd.svg"
OUT_PNG  = HTML_DIR / "fig_topology_padd.png"


# EIA's canonical state-to-PADD assignment.
# Source: https://www.eia.gov/petroleum/refinerycapacity/refmap.php
PADD_STATES = {
    "PADD1": ["CT", "DE", "FL", "GA", "ME", "MD", "MA", "NH", "NJ", "NY",
              "NC", "PA", "RI", "SC", "VT", "VA", "WV", "DC"],
    "PADD2": ["IL", "IN", "IA", "KS", "KY", "MI", "MN", "MO", "NE",
              "ND", "OH", "OK", "SD", "TN", "WI"],
    "PADD3": ["AL", "AR", "LA", "MS", "NM", "TX"],
    "PADD4": ["CO", "ID", "MT", "UT", "WY"],
    "PADD5": ["AK", "AZ", "CA", "HI", "NV", "OR", "WA"],
}

# Pastel fills, ordered to print legibly. PADD index 1-5 maps onto the
# discrete colorscale below.
PADD_COLORS = {
    "PADD1": "#cfe2f3",
    "PADD2": "#fce5cd",
    "PADD3": "#d9ead3",
    "PADD4": "#fff2cc",
    "PADD5": "#ead1dc",
}
PADD_LABELS = {
    "PADD1": "PADD 1 (East Coast)",
    "PADD2": "PADD 2 (Midwest)",
    "PADD3": "PADD 3 (Gulf Coast)",
    "PADD4": "PADD 4 (Rocky Mountains)",
    "PADD5": "PADD 5 (West Coast)",
}

# Functional groups: production / storage / pipeline / refineries. One colour
# and one shape per group; saturated palette to read cleanly on the pastel
# PADD fills. The subtype-to-group mapping below decides which bucket each
# node_subtype falls into.
GROUP_STYLE = {
    "production": {"color": "#1b7837", "symbol": "hexagon",     "size": 12, "open": False, "label": "Production"},
    "storage":    {"color": "#2166ac", "symbol": "diamond",     "size": 11, "open": False, "label": "Storage"},
    "pipeline":   {"color": "#4d4d4d", "symbol": "circle-open", "size":  7, "open": True,  "label": "Pipeline"},
    "refineries": {"color": "#b2182b", "symbol": "square",      "size":  8, "open": False, "label": "Refineries"},
}
SUBTYPE_TO_GROUP = {
    # production-side
    "state_conventional":           "production",
    "state_sub_basin":              "production",
    "offshore_region":              "production",
    "gathering":                    "production",
    # storage-side (anything that holds inventory and is not a refinery)
    "origin_terminal":              "storage",
    "storage_terminal":             "storage",
    "spr_site":                     "storage",
    "import_terminal":              "storage",
    "export_terminal":              "storage",
    # transport
    "pipeline":                     "pipeline",
    # processing
    "refinery":                     "refineries",
}
# Legend ordering: production → storage → pipeline → refineries
GROUP_ORDER = ["production", "storage", "pipeline", "refineries"]


def fetch_physical_nodes():
    """Pull physical assets with coords + PADD, and the physical-to-physical
    flow edges from v_flow_edges. Infer coords for pipelines from physical
    neighbours (same approach as make_map.py)."""
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.asset_id, a.name, a.node_subtype,
                   l.lat, l.lon, l.padd, l.country
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.kind = 'physical'
            ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        nodes = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT e.source, e.target
            FROM oil_network.v_flow_edges e
            JOIN oil_network.assets sa ON sa.asset_id = e.source
            JOIN oil_network.assets ta ON ta.asset_id = e.target
            WHERE sa.kind = 'physical' AND ta.kind = 'physical'
        """)
        edges = [(s, t) for s, t in cur.fetchall()]

    coords = {n["asset_id"]: (n["lat"], n["lon"])
              for n in nodes if n["lat"] is not None}
    neighbours = defaultdict(set)
    for s, t in edges:
        neighbours[s].add(t); neighbours[t].add(s)

    # Three passes: average neighbours that already have coords (handles
    # pipeline-pipeline edges).
    for _ in range(3):
        changed = False
        for n in nodes:
            nid = n["asset_id"]
            if nid in coords:
                continue
            nb = [coords[x] for x in neighbours.get(nid, []) if x in coords]
            if nb:
                n["lat"] = sum(c[0] for c in nb) / len(nb)
                n["lon"] = sum(c[1] for c in nb) / len(nb)
                coords[nid] = (n["lat"], n["lon"])
                changed = True
        if not changed:
            break
    return nodes, edges


def build_figure(nodes, edges):
    # Filter: must have coords + PADD + country US (drops Canadian/foreign).
    plotted = [
        n for n in nodes
        if n["lat"] is not None and n["lon"] is not None
        and n["padd"] in PADD_STATES
        and n["node_subtype"] in SUBTYPE_TO_GROUP
    ]
    by_group = defaultdict(list)
    for n in plotted:
        by_group[SUBTYPE_TO_GROUP[n["node_subtype"]]].append(n)
    coords = {n["asset_id"]: (n["lat"], n["lon"]) for n in plotted}

    fig = go.Figure()

    # PADD region shading via choropleth (Plotly bundles US-states geometry,
    # no external GeoJSON needed).
    for padd, states in PADD_STATES.items():
        fig.add_trace(go.Choropleth(
            locationmode="USA-states",
            locations=states,
            z=[1] * len(states),
            colorscale=[[0, PADD_COLORS[padd]], [1, PADD_COLORS[padd]]],
            showscale=False,
            marker_line_color="#9aa0a6",
            marker_line_width=0.6,
            name=PADD_LABELS[padd],
            hoverinfo="skip",
            showlegend=True,
            # Trick: a dummy z gives a coloured legend swatch for the region.
            legendgroup="padd",
            legendgrouptitle_text="PADD regions" if padd == "PADD1" else None,
        ))

    # Flow edges: one concatenated scattergeo line trace with None separators
    # between segments (same style as make_map.py: thin translucent grey so the
    # network topology reads without overwhelming the nodes).
    edge_lat, edge_lon = [], []
    n_edges = 0
    for s, t in edges:
        if s not in coords or t not in coords:
            continue
        edge_lat.extend([coords[s][0], coords[t][0], None])
        edge_lon.extend([coords[s][1], coords[t][1], None])
        n_edges += 1
    if n_edges:
        fig.add_trace(go.Scattergeo(
            lat=edge_lat, lon=edge_lon,
            mode="lines",
            line=dict(width=0.7, color="rgba(60, 60, 60, 0.35)"),
            hoverinfo="skip",
            name=f"Flow edges ({n_edges})",
            showlegend=True,
            legendgroup="edges",
        ))

    # One scattergeo trace per functional group: production / storage /
    # pipeline / refineries.
    for group in GROUP_ORDER:
        ns = by_group.get(group, [])
        if not ns:
            continue
        style = GROUP_STYLE[group]
        fig.add_trace(go.Scattergeo(
            lat=[n["lat"] for n in ns],
            lon=[n["lon"] for n in ns],
            mode="markers",
            marker=dict(
                symbol=style["symbol"],
                size=style["size"],
                color=style["color"] if not style["open"] else "rgba(0,0,0,0)",
                line=dict(width=1.2, color=style["color"]),
            ),
            name=f'{style["label"]} ({len(ns)})',
            text=[f'{n["asset_id"]}<br>{n["name"] or ""}' for n in ns],
            hovertemplate="%{text}<extra></extra>",
            legendgroup="nodes",
            legendgrouptitle_text="Physical nodes" if group == GROUP_ORDER[0] else None,
        ))

    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            # natural-earth keeps Alaska at its true geographic position
            # instead of insetting it the way 'albers usa' does. The lat/lon
            # range clips to a tight US viewport including Alaska.
            projection_type="natural earth",
            lonaxis_range=[-172, -65],
            lataxis_range=[17, 72],
            showland=True, landcolor="#f7f7f5",
            showsubunits=False,
            showcoastlines=True, coastlinecolor="#8a8e93",
            showlakes=True, lakecolor="#e9eef3",
            showcountries=True, countrycolor="#8a8e93",
            bgcolor="white",
        ),
        legend=dict(
            x=1.0, y=1.0, xanchor="right", yanchor="top",
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#cccccc", borderwidth=1,
            font=dict(size=11),
            groupclick="toggleitem",
        ),
        paper_bgcolor="white",
        plot_bgcolor="white",
        width=1100, height=720,
    )
    return fig, len(plotted)


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    nodes, edges = fetch_physical_nodes()
    fig, n_plotted = build_figure(nodes, edges)

    fig.write_html(str(OUT_HTML), include_plotlyjs="cdn", full_html=True)
    # kaleido is required for static export; it's installed in the venv.
    fig.write_image(str(OUT_SVG), format="svg", width=1100, height=720, scale=1)
    fig.write_image(str(OUT_PNG), format="png", width=1100, height=720, scale=2)

    print(f"Plotted {n_plotted} / {len(nodes)} physical nodes")
    print(f"HTML: {OUT_HTML}")
    print(f"SVG : {OUT_SVG}")
    print(f"PNG : {OUT_PNG}")


if __name__ == "__main__":
    main()
