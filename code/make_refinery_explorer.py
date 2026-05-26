"""Generate a self-contained HTML explorer for the `refineries.*` schema.

For each refinery the agent has populated, this renders:

  - a left-pane list (filterable by name / corp / PADD / state)
  - a detail pane on the right with: facts panel, Leaflet map of the site,
    Chart.js line graph of the monthly crude_runs_bpd series, and tables for
    process units, slate, events, sources, and exploration runs.

The HTML is fully self-contained — all data is baked in at generation time;
Leaflet and Chart.js load from CDN. Re-run this script after each agent
invocation to refresh the page. In production with many concurrent users we
will replace this with a DB-backed server.

Run from `code/`:

    ..\\..\\.venv\\Scripts\\python.exe make_refinery_explorer.py

Output: outputs/html/oil_network_refinery_explorer.html
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg2

from paths import HTML_DIR


HTML_OUT = HTML_DIR / "oil_network_refinery_explorer.html"

_DB_KW = dict(
    host="localhost", dbname="eia_crude",
    user="eia_user", password="eia_password",
)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"unserialisable: {type(obj)}")


def _rows(cur, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_payload() -> dict[str, Any]:
    """Bundle everything the page needs into a single dict.

    Top-level keys:
      refineries        — one row per refinery in refineries.refinery, joined
                          with lat/lon from oil_network.locations
      units             — { refinery_id: [process unit rows] }
      slate             — { refinery_id: [slate rows] }
      events            — { refinery_id: [event rows] }
      financials        — { refinery_id: [financial rows] }
      monthly           — { refinery_id: [runs_monthly rows ordered by month] }
      sources           — { refinery_id: [source rows] }
      runs              — { refinery_id: [exploration_runs rows] }
      commodities       — list of refineries.commodities rows (for legend)
    """
    with psycopg2.connect(**_DB_KW) as conn, conn.cursor() as cur:
        refineries = _rows(cur, """
            SELECT r.refinery_id, r.name, r.corporation, r.operator, r.site,
                   r.state, r.padd, r.rdist_label, r.capacity_bpd,
                   r.duoarea_code, r.primary_commodity, r.last_explored_at,
                   l.lat, l.lon, l.county
            FROM refineries.refinery r
            LEFT JOIN oil_network.nodes n     ON n.node_id = r.refinery_id
            LEFT JOIN oil_network.assets a    ON a.asset_id = n.asset_id
            LEFT JOIN oil_network.locations l ON l.location_id = a.location_id
            ORDER BY r.padd NULLS LAST, r.state, r.name
        """)

        def _bucket(table_sql: str, order_by: str = "") -> dict[str, list[dict[str, Any]]]:
            sql = f"SELECT * FROM refineries.{table_sql}"
            if order_by:
                sql += f" ORDER BY {order_by}"
            out: dict[str, list[dict[str, Any]]] = {}
            for row in _rows(cur, sql):
                out.setdefault(row["refinery_id"], []).append(row)
            return out

        units      = _bucket("process_units",   "refinery_id, unit_type")
        slate      = _bucket("slate",           "refinery_id, period_start, grade_name")
        events     = _bucket("events",          "refinery_id, start_date NULLS LAST")
        financials = _bucket("financials",      "refinery_id, period_start")
        runs_ts    = _bucket("runs_ts",         "refinery_id, metric, observation_date")
        sources    = _bucket("sources",         "refinery_id, source_id")
        runs       = _bucket("exploration_runs","refinery_id, run_id")
        slate_dist = _bucket("slate_distribution_ts",
                             "refinery_id, observation_date, commodity")
        by_grade   = _bucket("runs_by_grade_ts",
                             "refinery_id, observation_date, metric, commodity")

        commodities = _rows(cur, """
            SELECT commodity, description, sweet_sour, density_class,
                   api_gravity_min, api_gravity_max,
                   sulfur_pct_min, sulfur_pct_max,
                   region, typical_basin, discovered_in_run, discovered_at
            FROM refineries.commodities
            ORDER BY commodity
        """)

    return {
        "refineries":  refineries,
        "units":       units,
        "slate":       slate,
        "events":      events,
        "financials":  financials,
        "runs_ts":     runs_ts,
        "sources":     sources,
        "runs":        runs,
        "slate_dist":  slate_dist,
        "by_grade":    by_grade,
        "commodities": commodities,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<title>Refinery Explorer</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
      crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin=""></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #fafaf7;
    --panel: #ffffff;
    --line: #e5e3dd;
    --text: #232323;
    --muted: #6f6c66;
    --accent: #b13a23;
    --accent-soft: #f5e7e3;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
         font-size: 13px; color: var(--text); background: var(--bg); }
  header { padding: 10px 18px; background: var(--panel); border-bottom: 1px solid var(--line);
           display: flex; align-items: baseline; gap: 18px; }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  header .meta { color: var(--muted); font-size: 11px; }
  .layout { display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 44px); }
  aside { background: var(--panel); border-right: 1px solid var(--line); overflow: hidden;
          display: flex; flex-direction: column; }
  aside .filter { padding: 10px; border-bottom: 1px solid var(--line); }
  aside input { width: 100%; padding: 6px 8px; border: 1px solid var(--line); border-radius: 4px;
                font-size: 13px; }
  aside ul { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; }
  aside li { padding: 8px 12px; border-bottom: 1px solid var(--line); cursor: pointer; }
  aside li:hover { background: var(--accent-soft); }
  aside li.selected { background: var(--accent-soft); border-left: 3px solid var(--accent); }
  aside li .name { font-weight: 500; }
  aside li .sub { color: var(--muted); font-size: 11px; }
  main { overflow-y: auto; padding: 14px 18px; }
  main h2 { margin: 0 0 4px; font-size: 18px; font-weight: 600; }
  main .header-sub { color: var(--muted); margin-bottom: 14px; font-size: 12px; }
  .panels { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 10px 14px; }
  .panel h3 { margin: 0 0 6px; font-size: 12px; font-weight: 600; text-transform: uppercase;
              color: var(--muted); letter-spacing: 0.04em; }
  .kv { display: grid; grid-template-columns: 130px 1fr; gap: 4px 10px; font-size: 12px; }
  .kv dt { color: var(--muted); }
  .kv dd { margin: 0; }
  #map { height: 220px; width: 100%; border: 1px solid var(--line); border-radius: 4px; }
  #chart-wrap, #slate-chart-wrap { background: var(--panel); border: 1px solid var(--line); border-radius: 4px;
                                   padding: 10px 14px; margin-bottom: 14px; }
  #chart-wrap h3, #slate-chart-wrap h3 { margin: 0 0 6px; font-size: 12px; font-weight: 600; text-transform: uppercase;
                                          color: var(--muted); letter-spacing: 0.04em; }
  #chart { width: 100% !important; height: 250px !important; }
  #slate-chart { width: 100% !important; height: 200px !important; }
  .tables { display: grid; grid-template-columns: 1fr; gap: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  table caption { text-align: left; font-weight: 600; text-transform: uppercase; font-size: 11px;
                  color: var(--muted); margin-bottom: 4px; letter-spacing: 0.04em; }
  th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
           vertical-align: top; }
  th { color: var(--muted); font-weight: 500; }
  .empty { color: var(--muted); font-style: italic; padding: 6px 0; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px;
           background: var(--accent-soft); color: var(--accent); }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>
</head>
<body>
<header>
  <h1>US Refinery Explorer</h1>
  <span class="meta">Generated <span id="gen-at"></span> · <span id="ref-count"></span> refineries</span>
</header>
<div class="layout">
  <aside>
    <div class="filter"><input id="filter" placeholder="filter by name, corp, PADD, state..."></div>
    <ul id="refinery-list"></ul>
  </aside>
  <main id="detail">
    <p class="empty">Select a refinery on the left to see details.</p>
  </main>
</div>
<script>
const DATA = __PAYLOAD__;
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

document.getElementById("gen-at").textContent = DATA.generated_at.replace("T", " ").replace("Z", " UTC");
document.getElementById("ref-count").textContent = DATA.refineries.length;

const listEl = document.getElementById("refinery-list");
function renderList(filter) {
  listEl.innerHTML = "";
  const f = (filter || "").toLowerCase();
  DATA.refineries.forEach((r) => {
    const blob = [r.refinery_id, r.name, r.corporation, r.padd, r.state]
      .filter(Boolean).join(" ").toLowerCase();
    if (f && !blob.includes(f)) return;
    const li = document.createElement("li");
    li.dataset.id = r.refinery_id;
    li.innerHTML = `<div class="name">${escape(r.name || r.refinery_id)}</div>` +
                   `<div class="sub">${escape(r.padd || "")} · ${escape(r.state || "")} · ` +
                   `${fmtKb(r.capacity_bpd)} kbpd</div>`;
    li.onclick = () => selectRefinery(r.refinery_id);
    listEl.appendChild(li);
  });
}

document.getElementById("filter").addEventListener("input", (e) => renderList(e.target.value));
renderList("");

let mapInstance = null;
let chartInstance = null;
let slateChartInstance = null;

// Stable, deterministic colour per commodity_id. Cycles through a small
// curated palette by hashing the id.
const COMMODITY_PALETTE = [
  "#b13a23", "#3a73b1", "#3aa162", "#b18d3a", "#7a3ab1",
  "#3ab1a9", "#b1413a", "#5a8c2f", "#8c2f5a", "#2f5a8c",
];
function commodityColor(commodity) {
  let h = 0;
  for (let i = 0; i < commodity.length; i++) {
    h = (h * 31 + commodity.charCodeAt(i)) >>> 0;
  }
  return COMMODITY_PALETTE[h % COMMODITY_PALETTE.length];
}

function selectRefinery(rid) {
  $$("#refinery-list li").forEach((el) => el.classList.toggle("selected", el.dataset.id === rid));
  const r = DATA.refineries.find((x) => x.refinery_id === rid);
  if (!r) return;
  const units = DATA.units[rid] || [];
  const slate = DATA.slate[rid] || [];
  const events = DATA.events[rid] || [];
  const financials = DATA.financials[rid] || [];
  const runsTs = DATA.runs_ts[rid] || [];
  const sources = DATA.sources[rid] || [];
  const runs = DATA.runs[rid] || [];
  const slateDist = DATA.slate_dist[rid] || [];
  const byGrade   = DATA.by_grade[rid]   || [];

  const detail = document.getElementById("detail");
  detail.innerHTML = `
    <h2>${escape(r.name || r.refinery_id)}</h2>
    <div class="header-sub">
      <code>${escape(r.refinery_id)}</code> · ${escape(r.corporation || "(unknown corp)")} ·
      operator ${escape(r.operator || "?")} · ${escape(r.padd || "?")} / ${escape(r.state || "?")} ·
      ${fmtKb(r.capacity_bpd)} kbpd · last explored ${r.last_explored_at ? r.last_explored_at.slice(0, 16).replace("T", " ") : "—"}
    </div>
    <div class="panels">
      <div class="panel">
        <h3>Facts</h3>
        <dl class="kv">
          <dt>Refinery ID</dt><dd><code>${escape(r.refinery_id)}</code></dd>
          <dt>Site</dt><dd>${escape(r.site || "—")}</dd>
          <dt>County</dt><dd>${escape(r.county || "—")}</dd>
          <dt>Refining district</dt><dd>${escape(r.rdist_label || "—")}</dd>
          <dt>EIA duoarea</dt><dd><code>${escape(r.duoarea_code || "—")}</code></dd>
          <dt>Capacity (bpd)</dt><dd>${r.capacity_bpd != null ? r.capacity_bpd.toLocaleString() : "—"}</dd>
          <dt>Primary commodity</dt><dd><span class="badge">${escape(r.primary_commodity || "crude")}</span></dd>
        </dl>
      </div>
      <div class="panel">
        <h3>Location</h3>
        <div id="map"></div>
        <div style="font-size:11px;color:var(--muted);margin-top:6px">
          ${r.lat != null && r.lon != null
            ? `${r.lat.toFixed(4)}, ${r.lon.toFixed(4)}`
            : "<em>no coordinates in oil_network.locations</em>"}
        </div>
      </div>
    </div>
    <div id="chart-wrap">
      <h3>Consumption time-series (crude_runs_bpd, LOCF)</h3>
      <canvas id="chart"></canvas>
      <div style="font-size:11px;color:var(--muted);margin-top:6px" id="chart-meta"></div>
    </div>
    <div id="slate-chart-wrap">
      <h3>Slate distribution time-series (probability per commodity, step-wise)</h3>
      <canvas id="slate-chart"></canvas>
      <div style="font-size:11px;color:var(--muted);margin-top:6px" id="slate-chart-meta"></div>
    </div>
    <div class="tables">
      ${tableSection("Process units", units, ["unit_type","capacity_bpd","status","source_id"])}
      ${tableSection("Slate observations (period-level)", slate, ["period_start","period_end","grade_name","commodity","api_gravity","sulphur_pct","share_pct","run_id"])}
      ${tableSection("Throughput by grade — TS observations (top 24)", runsByGrade.slice(0, 24), ["observation_date","commodity","value","source","run_id"])}
      ${tableSection("Events", events, ["event_type","start_date","end_date","units_affected","capacity_impact_bpd","description"])}
      ${tableSection("Financial periods", financials, ["period_start","period_type","throughput_bpd","utilisation_pct","revenue_usd_m","ebitda_usd_m"])}
      ${tableSection("Sources", sources, ["document_type","title","publisher","url","published_at"])}
      ${tableSection("Exploration runs", runs, ["run_id","started_at","finished_at","status","model","tool_calls","cost_usd"])}
    </div>
  `;

  // Map
  if (mapInstance) { mapInstance.remove(); mapInstance = null; }
  if (r.lat != null && r.lon != null) {
    mapInstance = L.map("map").setView([r.lat, r.lon], 7);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap", maxZoom: 17,
    }).addTo(mapInstance);
    L.marker([r.lat, r.lon]).addTo(mapInstance)
      .bindPopup(`${r.name || r.refinery_id}<br>${fmtKb(r.capacity_bpd)} kbpd`);
  } else {
    document.getElementById("map").innerHTML =
      '<div style="padding:14px;color:var(--muted);font-style:italic">No coordinates available.</div>';
  }

  // Production chart — stacked area of per-grade throughput.
  // Top of the stack equals the aggregate runs_ts value; each colour band
  // is one commodity's bpd. Step interpolation reflects LOCF semantics.
  // Falls back to the aggregate line if no per-grade decomposition exists.
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
  const runsByGrade = byGrade.filter((g) => g.metric === "crude_runs_bpd");
  const runsAgg = runsTs.filter((m) => m.metric === "crude_runs_bpd");
  const chartMeta = document.getElementById("chart-meta");

  if (runsByGrade.length > 0) {
    const dates = Array.from(new Set(runsByGrade.map((g) => g.observation_date))).sort();
    const commoditiesInChart = Array.from(new Set(runsByGrade.map((g) => g.commodity))).sort();
    const byKey = new Map();
    runsByGrade.forEach((g) => byKey.set(`${g.observation_date}|${g.commodity}`, Number(g.value)));
    const datasets = commoditiesInChart.map((c) => ({
      label: c,
      data: dates.map((d) => byKey.get(`${d}|${c}`) || 0),
      borderColor: commodityColor(c),
      backgroundColor: commodityColor(c) + "cc",
      fill: true,
      stepped: "before",
      pointRadius: 1.5,
      stack: "throughput",
    }));
    chartMeta.innerHTML = `${dates.length} observations · ${commoditiesInChart.length} commodit${commoditiesInChart.length === 1 ? "y" : "ies"} · stacked step area (LOCF), top edge = aggregate throughput`;
    const ctx = document.getElementById("chart").getContext("2d");
    chartInstance = new Chart(ctx, {
      type: "line",
      data: { labels: dates, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: (item) => `${item.dataset.label}: ${item.parsed.y.toLocaleString()} bpd`,
            },
          },
        },
        scales: {
          x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
          y: { stacked: true, beginAtZero: true, title: { display: true, text: "bpd" } },
        },
      },
    });
  } else if (runsAgg.length > 0) {
    const labels = runsAgg.map((p) => p.observation_date);
    const values = runsAgg.map((p) => Number(p.value));
    chartMeta.innerHTML = `${runsAgg.length} aggregate observations · no per-grade decomposition (run derive_runs_by_grade_ts or wait for slate_distribution_ts to populate)`;
    const ctx = document.getElementById("chart").getContext("2d");
    chartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "crude_runs_bpd (aggregate)",
          data: values,
          borderColor: "#b13a23",
          backgroundColor: "rgba(177,58,35,0.1)",
          tension: 0.15, pointRadius: 2,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
          y: { beginAtZero: false, title: { display: true, text: "bpd" } },
        },
      },
    });
  } else {
    chartMeta.innerHTML = "<em>no crude_runs_bpd points yet — re-run the explorer to populate.</em>";
  }

  // Slate distribution stacked bar
  if (slateChartInstance) { slateChartInstance.destroy(); slateChartInstance = null; }
  const slateMeta = document.getElementById("slate-chart-meta");
  if (slateDist.length === 0) {
    slateMeta.innerHTML = "<em>no slate_distribution_ts rows yet — backfill via derive_slate_distribution_ts() or wait for the next agent run.</em>";
  } else {
    const dates = Array.from(new Set(slateDist.map((d) => d.observation_date))).sort();
    const commodities = Array.from(new Set(slateDist.map((d) => d.commodity))).sort();
    const byKey = new Map();
    slateDist.forEach((d) => byKey.set(`${d.observation_date}|${d.commodity}`, Number(d.probability)));
    const methods = new Set(slateDist.map((d) => d.method || "—"));
    const datasets = commodities.map((c) => ({
      label: c,
      data: dates.map((d) => byKey.get(`${d}|${c}`) || 0),
      backgroundColor: commodityColor(c),
      borderWidth: 0,
      stack: "slate",
    }));
    slateMeta.innerHTML = `${dates.length} observations · ${commodities.length} commodit${commodities.length === 1 ? "y" : "ies"} · methods: ${Array.from(methods).join(", ")}`;
    const ctx2 = document.getElementById("slate-chart").getContext("2d");
    slateChartInstance = new Chart(ctx2, {
      type: "bar",
      data: { labels: dates, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: (item) => `${item.dataset.label}: ${(item.parsed.y * 100).toFixed(1)}%`,
            },
          },
        },
        scales: {
          x: { stacked: true, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
          y: { stacked: true, min: 0, max: 1,
               ticks: { callback: (v) => (v * 100).toFixed(0) + "%" },
               title: { display: true, text: "share" } },
        },
      },
    });
  }
}

function tableSection(title, rows, cols) {
  if (!rows || rows.length === 0) {
    return `<table><caption>${escape(title)}</caption><tbody><tr><td class="empty">none recorded</td></tr></tbody></table>`;
  }
  const head = cols.map((c) => `<th>${escape(c)}</th>`).join("");
  const body = rows.map((r) =>
    "<tr>" + cols.map((c) => `<td>${renderCell(c, r[c])}</td>`).join("") + "</tr>"
  ).join("");
  return `<table><caption>${escape(title)}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderCell(col, val) {
  if (val == null) return "—";
  if (typeof val === "number") {
    if (col.endsWith("_pct")) return val.toFixed(2);
    if (col.includes("bpd") || col.includes("revenue") || col.includes("ebitda"))
      return val.toLocaleString();
    return val.toString();
  }
  if (typeof val === "string") {
    if (col === "url" && val.startsWith("http")) {
      return `<a href="${escape(val)}" target="_blank" rel="noopener">${escape(val.slice(0, 60))}…</a>`;
    }
    if (val.length > 200) return escape(val.slice(0, 200)) + "…";
    return escape(val);
  }
  return escape(String(val));
}

function fmtKb(bpd) {
  if (bpd == null) return "?";
  return (bpd / 1000).toFixed(0);
}

function escape(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Auto-select the first refinery so the page isn't empty on load
if (DATA.refineries.length > 0) selectRefinery(DATA.refineries[0].refinery_id);
</script>
</body>
</html>
"""


def main() -> None:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    payload = fetch_payload()
    payload_json = json.dumps(payload, default=_json_default)
    html = HTML_TEMPLATE.replace("__PAYLOAD__", payload_json)
    HTML_OUT.write_text(html, encoding="utf-8")
    n_refineries  = len(payload["refineries"])
    n_with_runs   = sum(1 for r in payload["refineries"] if payload["runs"].get(r["refinery_id"]))
    n_monthly     = sum(len(v) for v in payload["runs_ts"].values())
    print(f"wrote {HTML_OUT}")
    print(f"  refineries: {n_refineries}  (explored: {n_with_runs})")
    print(f"  monthly points (all metrics): {n_monthly}")
    print(f"  commodities: {len(payload['commodities'])}")
    print(f"  size: {HTML_OUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
