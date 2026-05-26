"""Generate refinery_grade_map.html — interactive map of every physical US
refinery, with click-to-inspect side panel showing the refinery's attributes
and its assigned crude grades from the `refineries` schema.

Reads from:
  - oil_network.assets         (refinery roster + configuration JSONB)
  - oil_network.locations      (lat / lon / state / PADD)
  - refineries.v_refinery_grades (per-refinery grade assignments
                                  pre-joined with grade metadata)

Output: outputs/html/refinery_grade_map.html

Matches the visual chrome of oil_network_partition_map.html:
  - dark header bar, side panel on the right, monospace asset_id
  - Plotly scattergeo (USA states, no external tile provider)
  - click a marker -> side panel fills with refinery + grade detail

This is reference-data only -- no scenario / resolver dependency.

Run via:

    ..\\..\\.venv\\Scripts\\python.exe code\\make_refinery_grade_map.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import psycopg2

from paths import HTML_DIR

DB = dict(host="localhost", dbname="eia_crude", user="eia_user", password="eia_password")
HTML_OUT = HTML_DIR / "refinery_grade_map.html"


SLATE_COLOR = {
    "heavy_sour":  "#7f1d1d",
    "medium_sour": "#c2410c",
    "light_sour":  "#d97706",
    "light_sweet": "#15803d",
}
SLATE_COLOR_DEFAULT = "#9ca3af"


def fetch_payload() -> dict:
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute(r"""
            SELECT
                a.asset_id,
                a.name,
                l.lat, l.lon, l.state, l.padd,
                a.attributes->'configuration'->>'site'       AS site,
                a.attributes->'configuration'->>'operator'   AS operator,
                (a.attributes->'configuration'->>'capacity_bd')::NUMERIC AS capacity_bd,
                (a.attributes->'configuration'->>'nelson_complexity_index')::NUMERIC AS nci,
                a.attributes->'configuration'->>'preferred_slate' AS preferred_slate,
                (a.attributes->'configuration'->>'has_coker')::BOOLEAN AS has_coker,
                (a.attributes->'configuration'->>'has_hydrocracker')::BOOLEAN AS has_hydrocracker,
                a.attributes->'configuration'->>'rdist_label' AS rdist_label,
                a.attributes->'configuration'->>'duoarea_code' AS duoarea_code
            FROM oil_network.assets a
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            WHERE a.asset_id LIKE 'ref\_%' ESCAPE '\'
              AND a.kind = 'physical'
            ORDER BY a.asset_id
        """)
        cols = [c.name for c in cur.description]
        refineries = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT
                refinery_id, commodity, commodity_description,
                sweet_sour, density_class,
                api_gravity_min, api_gravity_max,
                sulfur_pct_min,  sulfur_pct_max,
                typical_basin,
                is_primary, source, notes
            FROM refineries.v_refinery_grades
            ORDER BY refinery_id,
                     is_primary DESC,
                     commodity
        """)
        gcols = [c.name for c in cur.description]
        grades_rows = [dict(zip(gcols, r)) for r in cur.fetchall()]

    grades_by_ref: dict[str, list[dict]] = {}
    for g in grades_rows:
        grades_by_ref.setdefault(g["refinery_id"], []).append({
            "commodity":     g["commodity"],
            "description":   g["commodity_description"],
            "sweet_sour":    g["sweet_sour"],
            "density_class": g["density_class"],
            "api_min":       float(g["api_gravity_min"]) if g["api_gravity_min"] is not None else None,
            "api_max":       float(g["api_gravity_max"]) if g["api_gravity_max"] is not None else None,
            "sulfur_min":    float(g["sulfur_pct_min"])  if g["sulfur_pct_min"]  is not None else None,
            "sulfur_max":    float(g["sulfur_pct_max"])  if g["sulfur_pct_max"]  is not None else None,
            "basin":         g["typical_basin"],
            "is_primary":    bool(g["is_primary"]),
            "source":        g["source"],
            "notes":         g["notes"],
        })

    out: list[dict] = []
    for r in refineries:
        if r["lat"] is None or r["lon"] is None:
            continue
        out.append({
            "refinery_id":     r["asset_id"],
            "name":            r["name"],
            "site":            r["site"],
            "operator":        r["operator"],
            "lat":             float(r["lat"]),
            "lon":             float(r["lon"]),
            "state":           r["state"],
            "padd":            r["padd"],
            "capacity_bd":     float(r["capacity_bd"]) if r["capacity_bd"] is not None else None,
            "nci":             float(r["nci"]) if r["nci"] is not None else None,
            "preferred_slate": r["preferred_slate"],
            "has_coker":       r["has_coker"],
            "has_hydrocracker": r["has_hydrocracker"],
            "rdist_label":     r["rdist_label"],
            "duoarea_code":    r["duoarea_code"],
            "grades":          grades_by_ref.get(r["asset_id"], []),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_refineries": len(out),
        "n_grade_assignments": sum(len(r["grades"]) for r in out),
        "refineries": out,
        "slate_color": SLATE_COLOR,
        "slate_color_default": SLATE_COLOR_DEFAULT,
    }


# ----------------------------------------------------------------------------
# HTML template -- styling matched to oil_network_partition_map.html
# ----------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<!-- __BEACON__ -->
<meta charset="UTF-8">
<title>oil_network &mdash; refinery grade map</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { margin: 0; font: 13px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #f7f7f5; color: #222; }
  header { padding: 8px 16px; background: #1a1f2c; color: #fff;
           display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; }
  header .stats { margin-left: auto; font-size: 11.5px; color: #ccc; }
  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .controls label { color: #ccc; font-size: 11.5px; }
  .controls select, .controls input {
      background: #2a3050; color: #fff; border: 1px solid #4a5070;
      padding: 3px 7px; font: inherit; font-size: 12px; }
  .controls input { width: 200px; }
  .controls button {
      background: #2a3050; color: #fff; border: 1px solid #4a5070;
      padding: 3px 10px; cursor: pointer; font: inherit; font-size: 12px; }
  .controls button:hover { background: #3a4070; }
  #subhead { padding: 6px 16px; background: #ececea; border-bottom: 1px solid #d0d0c8;
             font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
             color: #444; }
  .layout { display: grid; grid-template-columns: 1fr 380px;
            height: calc(100vh - 38px - 30px); }
  #map { width: 100%; height: 100%; }
  #side { background: #fff; border-left: 1px solid #d0d0c8;
          padding: 14px 16px; overflow-y: auto; font-size: 12.5px; }
  #side h2 { margin: 0 0 4px 0; font-size: 14px;
             font-family: ui-monospace, Menlo, monospace; }
  #side h2 .name { font-family: -apple-system, "Segoe UI", sans-serif; font-size: 13px;
                   color: #555; display: block; margin-top: 2px; font-weight: 500; }
  #side h3 { margin: 14px 0 4px 0; font-size: 11px; color: #666;
             text-transform: uppercase; letter-spacing: 0.04em; }
  #side .row { display: grid; grid-template-columns: 110px 1fr; gap: 4px 8px; }
  #side .row .k { color: #777; }
  #side .row .v { font-family: ui-monospace, Menlo, monospace; word-break: break-all; }
  #side .placeholder { color: #999; padding: 60px 0; text-align: center; font-style: italic; }
  #side .hint { color: #666; font-size: 11.5px; padding: 8px; background: #f4f4f0;
                margin: 8px 0; border-radius: 3px; }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 9px;
          font-size: 10.5px; font-weight: 500; margin-right: 3px; vertical-align: middle; }
  .pill-slate-heavy_sour   { background:#7f1d1d; color:#fff; }
  .pill-slate-medium_sour  { background:#c2410c; color:#fff; }
  .pill-slate-light_sour   { background:#d97706; color:#fff; }
  .pill-slate-light_sweet  { background:#15803d; color:#fff; }
  .pill-slate-null         { background:#9ca3af; color:#fff; }
  .pill-yes                { background:#1e3a8a; color:#fff; }
  .pill-no                 { background:#e5e7eb; color:#6b7280; }
  .pill-primary            { background:#1e3a8a; color:#fff; }
  .pill-secondary          { background:#cbd5e1; color:#1f2937; }
  table.grades { width: 100%; border-collapse: collapse; font-size: 11.5px; margin-top: 4px; }
  table.grades th, table.grades td { text-align: left; padding: 4px 5px;
      border-bottom: 1px solid #f3f4f6; vertical-align: top; }
  table.grades th { background: #f9fafb; font-weight: 600;
      font-family: ui-monospace, Menlo, monospace; font-size: 10.5px;
      color: #555; text-transform: uppercase; letter-spacing: 0.03em; }
  table.grades tr.primary td { background: #f0f7ff; }
  table.grades .commodity {
      font-family: ui-monospace, Menlo, monospace; font-weight: 600; }
  .note { color: #6b7280; font-size: 10.5px; font-style: italic; padding-left: 8px; }
  .source-badge { background:#e5e7eb; color:#374151; padding:0 4px; border-radius:3px;
                  font-size:10px; font-family: ui-monospace, Menlo, monospace; }
</style>
</head>
<body>
<header>
  <h1>oil_network &middot; refinery grade map</h1>
  <div class="controls">
    <label>PADD
      <select id="padd-filter"><option value="">all</option></select>
    </label>
    <label>Slate
      <select id="slate-filter">
        <option value="">all</option>
        <option value="heavy_sour">heavy_sour</option>
        <option value="medium_sour">medium_sour</option>
        <option value="light_sweet">light_sweet</option>
        <option value="__null__">(unset)</option>
      </select>
    </label>
    <label>Coker
      <select id="coker-filter">
        <option value="">all</option>
        <option value="yes">has coker</option>
        <option value="no">no coker</option>
      </select>
    </label>
    <input id="search" type="text" placeholder="search name / operator / site / grade">
    <button id="reset">Reset</button>
  </div>
  <div class="stats">
    <b id="vis-n">__N_REFINERIES__</b> / __N_REFINERIES__ refineries
    &middot;
    <b>__N_ASSIGNMENTS__</b> grade assignments
  </div>
</header>
<div id="subhead">click a refinery marker to inspect &mdash; marker size = capacity, fill colour = preferred slate</div>
<div class="layout">
  <div id="map"></div>
  <aside id="side">
    <div class="placeholder">Click a refinery on the map to drill in.</div>
  </aside>
</div>
<script>
const DATA = __DATA__;
const SLATE_COLOR         = DATA.slate_color;
const SLATE_COLOR_DEFAULT = DATA.slate_color_default;

function slateColor(s) { return SLATE_COLOR[s] || SLATE_COLOR_DEFAULT; }
function markerSize(cap) {
    if (cap == null) return 7;
    return 6 + Math.sqrt(cap / 1000) * 1.0;   // ~6-22 px
}

const ALL = DATA.refineries.slice();
let visible = ALL.slice();

// Populate PADD filter
const padds = [...new Set(ALL.map(r => r.padd).filter(p => p != null))].sort();
const paddSel = document.getElementById("padd-filter");
for (const p of padds) {
    const opt = document.createElement("option");
    opt.value = p; opt.textContent = p; paddSel.appendChild(opt);
}

function buildTrace(rows) {
    return {
        type: "scattergeo",
        mode: "markers",
        lon: rows.map(r => r.lon),
        lat: rows.map(r => r.lat),
        marker: {
            size: rows.map(r => markerSize(r.capacity_bd)),
            color: rows.map(r => slateColor(r.preferred_slate)),
            line: { width: 0.6, color: "#1f2937" },
            opacity: 0.85,
            sizemode: "diameter",
        },
        text: rows.map(r => {
            const cap = r.capacity_bd ? `${(r.capacity_bd / 1000).toFixed(0)} kbd` : "";
            const slate = r.preferred_slate || "(slate unset)";
            return `<b>${r.name || r.refinery_id}</b><br>` +
                   (r.operator ? `${r.operator}<br>` : "") +
                   `${cap}${cap && slate ? " &middot; " : ""}${slate}<br>` +
                   `<span style="color:#888;">${r.refinery_id}</span>`;
        }),
        customdata: rows.map(r => r.refinery_id),
        hovertemplate: "%{text}<extra></extra>",
    };
}

const LAYOUT = {
    margin: { l: 0, r: 0, t: 0, b: 0 },
    geo: {
        scope: "north america",
        projection: { type: "albers usa" },
        showland: true,    landcolor: "#f7f7f5",
        showlakes: true,   lakecolor: "#dbe9f4",
        showcountries: true, countrycolor: "#bbb",
        showsubunits: true,  subunitcolor: "#cfcfcf",
        coastlinecolor: "#888",
        bgcolor: "#ffffff",
    },
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#ffffff",
    showlegend: false,
};

Plotly.newPlot("map", [buildTrace(visible)], LAYOUT, {
    responsive: true, displayModeBar: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d"],
    displaylogo: false,
});

document.getElementById("map").on("plotly_click", e => {
    const rid = e.points[0]?.customdata;
    if (!rid) return;
    const r = ALL.find(x => x.refinery_id === rid);
    if (r) showRefinery(r);
});

// -----------------------------------------------------------------
// Filter logic
// -----------------------------------------------------------------
function applyFilters() {
    const paddF  = paddSel.value;
    const slateF = document.getElementById("slate-filter").value;
    const cokerF = document.getElementById("coker-filter").value;
    const q      = document.getElementById("search").value.trim().toLowerCase();

    visible = ALL.filter(r => {
        if (paddF && r.padd !== paddF) return false;
        if (slateF === "__null__" && r.preferred_slate != null) return false;
        if (slateF && slateF !== "__null__" && r.preferred_slate !== slateF) return false;
        if (cokerF === "yes" && r.has_coker !== true) return false;
        if (cokerF === "no"  && r.has_coker === true) return false;
        if (q) {
            const hay = [
                r.name, r.operator, r.site, r.refinery_id, r.state,
                ...r.grades.map(g => g.commodity),
            ].filter(x => x).join(" ").toLowerCase();
            if (!hay.includes(q)) return false;
        }
        return true;
    });

    Plotly.react("map", [buildTrace(visible)], LAYOUT);
    document.getElementById("vis-n").textContent = visible.length;
}
paddSel.onchange = applyFilters;
document.getElementById("slate-filter").onchange = applyFilters;
document.getElementById("coker-filter").onchange = applyFilters;
document.getElementById("search").oninput = applyFilters;
document.getElementById("reset").onclick = () => {
    paddSel.value = "";
    document.getElementById("slate-filter").value = "";
    document.getElementById("coker-filter").value = "";
    document.getElementById("search").value = "";
    applyFilters();
};

// -----------------------------------------------------------------
// Side panel rendering
// -----------------------------------------------------------------
function fmtBool(b) {
    if (b === true)  return `<span class="pill pill-yes">yes</span>`;
    if (b === false) return `<span class="pill pill-no">no</span>`;
    return `<span style="color:#999;">&mdash;</span>`;
}
function fmtSlate(s) {
    if (!s) return `<span class="pill pill-slate-null">unset</span>`;
    return `<span class="pill pill-slate-${s}">${s}</span>`;
}
function fmtRange(lo, hi, unit) {
    if (lo == null && hi == null) return `<span style="color:#aaa;">&mdash;</span>`;
    return `${lo ?? "?"}&ndash;${hi ?? "?"}${unit}`;
}

function showRefinery(r) {
    const grades = r.grades || [];
    const side = document.getElementById("side");
    side.innerHTML = `
        <h2>${r.refinery_id}<span class="name">${r.name || ""}</span></h2>
        <h3>Refinery</h3>
        <div class="row">
          <div class="k">operator</div>   <div class="v">${r.operator ?? "<span style='color:#aaa;'>&mdash;</span>"}</div>
          <div class="k">site</div>       <div class="v">${r.site ?? "<span style='color:#aaa;'>&mdash;</span>"}</div>
          <div class="k">location</div>   <div class="v">${[r.state, r.padd].filter(x => x).join(" &middot; ") || "&mdash;"}</div>
          <div class="k">capacity</div>   <div class="v">${r.capacity_bd ? (r.capacity_bd/1000).toFixed(0) + " kbd" : "<span style='color:#aaa;'>&mdash;</span>"}</div>
          <div class="k">NCI</div>        <div class="v">${r.nci ?? "<span style='color:#aaa;'>&mdash;</span>"}</div>
          <div class="k">slate</div>      <div class="v">${fmtSlate(r.preferred_slate)}</div>
          <div class="k">coker</div>      <div class="v">${fmtBool(r.has_coker)}</div>
          <div class="k">hydrocracker</div><div class="v">${fmtBool(r.has_hydrocracker)}</div>
          <div class="k">EIA district</div><div class="v">${r.rdist_label ?? "<span style='color:#aaa;'>&mdash;</span>"} ${r.duoarea_code ? `<span class="source-badge">${r.duoarea_code}</span>` : ""}</div>
        </div>

        <h3>Assigned grades (${grades.length})</h3>
        ${grades.length === 0 ? "<div class='hint'>No grades assigned for this refinery.</div>" : ""}
        ${grades.length > 0 ? `
        <table class="grades">
          <thead>
            <tr>
              <th>Grade</th><th>S/S</th><th>Density</th><th>API</th><th>S %</th><th>Prim</th>
            </tr>
          </thead>
          <tbody>
            ${grades.map(g => `
              <tr class="${g.is_primary ? "primary" : ""}">
                <td class="commodity">${g.commodity}<div style="color:#888;font-size:10.5px;font-family:inherit;font-weight:400;">${g.basin ?? ""}</div></td>
                <td>${g.sweet_sour ?? "&mdash;"}</td>
                <td>${g.density_class ?? "&mdash;"}</td>
                <td>${fmtRange(g.api_min, g.api_max, "&deg;")}</td>
                <td>${fmtRange(g.sulfur_min, g.sulfur_max, "%")}</td>
                <td>${g.is_primary ? "<span class='pill pill-primary'>1&deg;</span>" : "<span class='pill pill-secondary'>2&deg;</span>"}</td>
              </tr>
              ${g.notes ? `<tr><td colspan="6" class="note">${g.notes} <span class="source-badge">${g.source ?? "?"}</span></td></tr>` : ""}
            `).join("")}
          </tbody>
        </table>` : ""}
    `;
}
</script>
</body>
</html>
"""


def main() -> None:
    print("[1] fetching refinery + grade payload from DB")
    payload = fetch_payload()
    print(f"    {payload['n_refineries']} refineries with coordinates, "
          f"{payload['n_grade_assignments']} grade assignments")

    beacon = json.dumps({
        "view": "refinery_grade_map",
        "generated_at": payload["generated_at"],
        "n_refineries": payload["n_refineries"],
        "n_grade_assignments": payload["n_grade_assignments"],
    }, separators=(",", ":"))

    print(f"[2] rendering HTML to {HTML_OUT}")
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    html = (HTML
        .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
        .replace("__N_REFINERIES__", str(payload["n_refineries"]))
        .replace("__N_ASSIGNMENTS__", str(payload["n_grade_assignments"]))
        .replace("__BEACON__", "oilnet-artefact: " + beacon))
    HTML_OUT.write_text(html, encoding="utf-8")
    size_kb = HTML_OUT.stat().st_size // 1024
    print(f"    wrote {size_kb} KB")


if __name__ == "__main__":
    main()
