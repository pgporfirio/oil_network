"""
Build an interactive HTML map (`crude_logistics_edges.html`) where each flow
edge is clickable/hoverable to inspect:
  - the two nodes it connects
  - the relational variables that *define* the edge (paired inflow/outflow)
  - each variable's binding under the active scope (TS-bound, formula, latent, etc.)

Data is pulled from `oil_network` (the database is the canonical source).
"""
import math
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import create_engine, text


def geodesic_midpoint(lat1, lon1, lat2, lon2):
    """Great-circle midpoint between two points (deg in / deg out).

    Plotly Scattergeo renders edges as geodesics, so a naive lat/lon mean
    sits off the rendered line for non-trivial spans. This formula is the
    standard great-circle midpoint (Bowring/Williams).
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    lam1, lam2 = math.radians(lon1), math.radians(lon2)
    dlam = lam2 - lam1
    bx = math.cos(phi2) * math.cos(dlam)
    by = math.cos(phi2) * math.sin(dlam)
    phi_m = math.atan2(
        math.sin(phi1) + math.sin(phi2),
        math.sqrt((math.cos(phi1) + bx) ** 2 + by ** 2),
    )
    lam_m = lam1 + math.atan2(by, math.cos(phi1) + bx)
    return math.degrees(phi_m), math.degrees(lam_m)

ROOT = Path(__file__).parent
OUT  = ROOT / "crude_logistics_edges.html"
PG_URL = "postgresql+psycopg2://eia_user:eia_password@localhost:5432/eia_crude"
SCENARIO_ID = "starter_us_crude_2015_2025"

engine = create_engine(PG_URL, future=True)


# -----------------------------------------------------------------------------
# 1. Pull nodes with coordinates
# -----------------------------------------------------------------------------
nodes = pd.read_sql(text("""
    SELECT
        a.asset_id  AS node_id,
        a.name,
        a.node_class,
        a.node_subtype,
        a.kind,
        a.starter_status,
        l.lat, l.lon,
        l.state, l.padd,
        a.attributes -> 'configuration' ->> 'capacity_bpd' AS capacity_bpd,
        a.attributes -> 'configuration' ->> 'capacity_bd'  AS capacity_bd,
        a.attributes -> 'configuration' ->> 'rdist_label'  AS district
    FROM oil_network.assets a
    LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
    WHERE l.lat IS NOT NULL AND l.lon IS NOT NULL
"""), engine)
print(f"Nodes with coords: {len(nodes)}")


# -----------------------------------------------------------------------------
# 2. Pull edges (relational variables, paired) with their assignments
# -----------------------------------------------------------------------------
edges = pd.read_sql(text("""
    WITH outv AS (
        SELECT v.node_id AS source, v.related_node_id AS target,
               v.commodity, v.variable_id AS outflow_var
        FROM oil_network.variables v
        WHERE v.variable_type = 'outflow' AND v.related_node_id IS NOT NULL
    ),
    inv AS (
        SELECT v.node_id AS target, v.related_node_id AS source,
               v.commodity, v.variable_id AS inflow_var
        FROM oil_network.variables v
        WHERE v.variable_type = 'inflow' AND v.related_node_id IS NOT NULL
    )
    SELECT
        outv.source, outv.target, outv.commodity,
        outv.outflow_var, inv.inflow_var,
        out_va.timeseries_id AS out_ts,
        out_va.formula       AS out_formula,
        in_va.timeseries_id  AS in_ts,
        in_va.formula        AS in_formula,
        ls.lat AS src_lat, ls.lon AS src_lon,
        lt.lat AS tgt_lat, lt.lon AS tgt_lon,
        sa.node_subtype AS src_subtype, ta.node_subtype AS tgt_subtype
    FROM outv
    JOIN inv ON inv.source = outv.source AND inv.target = outv.target
            AND inv.commodity = outv.commodity
    LEFT JOIN oil_network.variable_assignments out_va
        ON out_va.variable_id = outv.outflow_var AND out_va.scenario_id = :s
    LEFT JOIN oil_network.variable_assignments in_va
        ON in_va.variable_id = inv.inflow_var AND in_va.scenario_id = :s
    JOIN oil_network.assets sa ON sa.asset_id = outv.source
    JOIN oil_network.assets ta ON ta.asset_id = outv.target
    LEFT JOIN oil_network.locations ls ON ls.location_id = sa.location_id
    LEFT JOIN oil_network.locations lt ON lt.location_id = ta.location_id
    WHERE ls.lat IS NOT NULL AND lt.lat IS NOT NULL
"""), engine, params={"s": SCENARIO_ID})
print(f"Edges with both endpoints geocoded: {len(edges)}")


# -----------------------------------------------------------------------------
# 3. Build hover text per edge (and per node)
# -----------------------------------------------------------------------------
def binding_label(ts: str | None, formula: str | None) -> str:
    if ts is not None:
        return f"<b>observed</b> via {ts}"
    if formula is None:
        return "<i>unassigned</i>"
    if formula == "0":
        return "zero"
    if formula == "latent()":
        return "<i>latent</i>"
    if formula == "sum_over_children":
        return "rollup: sum_over_children"
    if formula == "sum_over_outflows":
        return "rollup: sum_over_outflows"
    return f"derived: {formula}"


edge_hover = []
for _, e in edges.iterrows():
    out_b = binding_label(e["out_ts"], e["out_formula"])
    in_b  = binding_label(e["in_ts"],  e["in_formula"])
    txt = (
        f"<b>{e['source']}</b> → <b>{e['target']}</b><br>"
        f"<i>{e['src_subtype']} → {e['tgt_subtype']}</i> &nbsp; commodity: {e['commodity']}<br>"
        f"<br>"
        f"<b>outflow var:</b> {e['outflow_var']}<br>"
        f"&nbsp;&nbsp;binding: {out_b}<br>"
        f"<b>inflow var:</b> {e['inflow_var']}<br>"
        f"&nbsp;&nbsp;binding: {in_b}"
    )
    edge_hover.append(txt)
edges = edges.assign(hover=edge_hover)


# Node hover
def node_hover_row(r):
    cap = r["capacity_bpd"] or r["capacity_bd"]
    cap_s = f"<br>capacity: {int(cap):,} BPCD" if cap and str(cap).isdigit() else ""
    return (
        f"<b>{r['node_id']}</b><br>"
        f"{r['name']}<br>"
        f"type: {r['node_subtype']}<br>"
        f"state: {r.get('state') or '—'} &nbsp; padd: {r.get('padd') or '—'}"
        f"{cap_s}"
    )
nodes["hover"] = nodes.apply(node_hover_row, axis=1)


# -----------------------------------------------------------------------------
# 4. Build the figure
# -----------------------------------------------------------------------------
fig = go.Figure()

# 4a. Edge lines — single trace, segments separated by None
edge_lats, edge_lons = [], []
for _, e in edges.iterrows():
    edge_lats += [e["src_lat"], e["tgt_lat"], None]
    edge_lons += [e["src_lon"], e["tgt_lon"], None]

fig.add_trace(go.Scattergeo(
    lat=edge_lats, lon=edge_lons,
    mode="lines",
    line=dict(width=0.6, color="rgba(70,70,90,0.55)"),
    hoverinfo="skip",  # hover handled by midpoint markers below
    name="edges (lines)",
    showlegend=False,
))

# 4b. Edge midpoint markers — hover-rich, click target. Use great-circle
# midpoint so the diamond sits on the rendered geodesic line (plotly's
# Scattergeo lines are geodesics, not straight lat/lon segments).
mid_lats, mid_lons = [], []
for _, e in edges.iterrows():
    mlat, mlon = geodesic_midpoint(e["src_lat"], e["src_lon"], e["tgt_lat"], e["tgt_lon"])
    mid_lats.append(mlat)
    mid_lons.append(mlon)

fig.add_trace(go.Scattergeo(
    lat=mid_lats, lon=mid_lons,
    mode="markers",
    marker=dict(size=6, color="rgba(70,70,90,0.5)", symbol="diamond",
                line=dict(width=0.5, color="rgba(0,0,0,0.4)")),
    text=edges["hover"],
    hovertemplate="%{text}<extra></extra>",
    name="edge midpoints (hover for details)",
    showlegend=True,
))

# 4c. Nodes — coloured by node_subtype
SUBTYPE_COLORS = {
    "refinery":               "#d62728",  # red
    "refining_district_view": "#9467bd",  # purple (abstract)
    "refining_centre_view":   "#9c5fb5",
    "padd_view":              "#9c5fb5",
    "observational_aggregate":"#9467bd",
    "state_sub_basin":        "#2ca02c",  # green production
    "state_conventional":     "#2ca02c",
    "offshore_region":        "#1f77b4",
    "foreign_production_aggregate": "#17becf",
    "gathering":              "#bcbd22",
    "origin_terminal":        "#ff7f0e",
    "storage_terminal":       "#8c564b",
    "spr_site":               "#7f7f7f",
    "export_terminal":        "#e377c2",
    "import_terminal":        "#1f77b4",
    "pipeline":               "#aaa",
}

for subtype, group in nodes.groupby("node_subtype"):
    color = SUBTYPE_COLORS.get(subtype, "#777")
    is_abstract = group["kind"].iloc[0] == "abstract" if "kind" in group else False
    size = 6 if is_abstract else 8
    fig.add_trace(go.Scattergeo(
        lat=group["lat"], lon=group["lon"],
        mode="markers",
        marker=dict(size=size, color=color, line=dict(width=0.5, color="rgba(0,0,0,0.5)"),
                    opacity=0.55 if is_abstract else 0.85),
        text=group["hover"],
        hovertemplate="%{text}<extra></extra>",
        name=f"{subtype} ({len(group)})",
    ))


# -----------------------------------------------------------------------------
# 5. Layout — US extent
# -----------------------------------------------------------------------------
fig.update_geos(
    scope="north america",
    showland=True, landcolor="rgb(243,243,243)",
    showcountries=True, countrycolor="rgb(204,204,204)",
    showsubunits=True, subunitcolor="rgb(220,220,220)",
    projection_type="azimuthal equal area",
    lonaxis_range=[-170, -55],
    lataxis_range=[15, 75],
    center=dict(lat=42, lon=-100),
)
fig.update_layout(
    title=dict(
        text=(
            "US crude logistics — interactive edge explorer<br>"
            "<sub>Hover an edge midpoint (diamond) to see the two relational variables (outflow on the source, "
            "inflow on the target) and their <b>binding</b> under scenario "
            f"<i>{SCENARIO_ID}</i>.<br>"
            "<b>Binding</b> = how the variable gets its value in this scenario: "
            "<b>observed</b> (linked to a timeseries), "
            "<b>zero</b> (structurally absent), "
            "<b>latent</b> (unobserved, free under mass-balance), "
            "<b>derived</b> (formula over other variables), "
            "or <i>unassigned</i>. Data sourced from <b>oil_network</b> (Postgres).</sub>"
        ),
        x=0.5, xanchor="center",
    ),
    legend=dict(
        title="Node types",
        yanchor="top", y=0.98, xanchor="left", x=0.01,
        bgcolor="rgba(255,255,255,0.85)",
    ),
    margin=dict(l=10, r=10, t=110, b=10),
    height=900,
)

# -----------------------------------------------------------------------------
# 6. Write
# -----------------------------------------------------------------------------
fig.write_html(OUT, include_plotlyjs="cdn", full_html=True)
print(f"Wrote {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")
print(f"Edges: {len(edges)}, Nodes: {len(nodes)}")
