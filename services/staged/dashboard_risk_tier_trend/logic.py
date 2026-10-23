from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime
from html import escape
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


API_URL = "/api/risk/trend?days=30"
PLOTLY_URL = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def risk_tier_histogram(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    counts: dict[str, int] = {}
    for tier, count in rows:
        key = str(tier).strip() if tier else "Unknown"
        counts[key] = int(count)
    return dict(sorted(counts.items(), key=lambda item: item[0].casefold()))


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return text


def _rows_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("series", "trends", "trend", "risk_tier_trend", "points", "rows", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _rows_from_payload(value)
            if nested:
                return nested
    if "date" in payload or "day" in payload or "timestamp" in payload:
        return [payload]
    return []


def normalize_trend_payload(payload: Any) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for row in _rows_from_payload(payload):
        if not isinstance(row, dict):
            continue
        day = _date_text(
            row.get("date") or row.get("day") or row.get("timestamp") or row.get("observed_at")
        )
        if not day:
            continue
        tier_counts = row.get("tier_counts")
        if not isinstance(tier_counts, dict):
            tier_counts = row.get("tiers") if isinstance(row.get("tiers"), dict) else None
        entries: list[tuple[Any, Any]]
        if tier_counts is not None:
            entries = list(tier_counts.items())
        else:
            tier = (
                row.get("tier")
                or row.get("risk_tier")
                or row.get("riskTier")
                or row.get("label")
                or "Unknown"
            )
            count = row.get("count", row.get("server_count", row.get("total", row.get("score", 0))))
            entries = [(tier, count)]
        for tier, raw_count in entries:
            if isinstance(raw_count, dict):
                raw_count = raw_count.get("count", raw_count.get("value"))
            try:
                numeric_count = float(raw_count)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(numeric_count) or numeric_count < 0:
                continue
            count = int(numeric_count)
            if count != numeric_count:
                continue
            tier_name = str(tier).strip() if tier else "Unknown"
            totals[(day, tier_name)] += count
    return [
        {"date": day, "tier": tier, "count": count}
        for (day, tier), count in sorted(totals.items(), key=lambda item: (item[0][0], item[0][1].casefold()))
    ]


def build_plotly_figure(trend_data: list[dict[str, Any]]) -> dict[str, Any]:
    rows = normalize_trend_payload(trend_data)
    dates = sorted({row["date"] for row in rows})
    tiers = sorted({row["tier"] for row in rows}, key=str.casefold)
    by_tier_date = {(row["tier"], row["date"]): row["count"] for row in rows}
    traces = [
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": tier,
            "x": dates,
            "y": [by_tier_date.get((tier, day), 0) for day in dates],
        }
        for tier in tiers
    ]
    return {
        "data": traces,
        "layout": {
            "title": {"text": "Risk Tier Trend"},
            "xaxis": {"title": {"text": "Date"}, "type": "date"},
            "yaxis": {"title": {"text": "Server Count"}, "rangemode": "tozero"},
            "hovermode": "x unified",
            "margin": {"t": 48, "r": 24, "b": 48, "l": 56},
            "legend": {"orientation": "h", "y": -0.2},
        },
    }


def build_summary_statistics(trend_data: list[dict[str, Any]]) -> dict[str, Any]:
    rows = normalize_trend_payload(trend_data)
    totals_by_date: dict[str, int] = defaultdict(int)
    for row in rows:
        totals_by_date[row["date"]] += row["count"]
    dates = sorted(totals_by_date)
    latest_date = dates[-1] if dates else "—"
    latest_total = totals_by_date[latest_date] if dates else 0
    average_total = sum(totals_by_date.values()) / len(dates) if dates else 0.0
    return {
        "observations": len(dates),
        "latest_date": latest_date,
        "latest_total": latest_total,
        "average_total": round(average_total, 2),
        "tiers": len({row["tier"] for row in rows}),
    }


def build_html_table(trend_data: list[dict[str, Any]]) -> str:
    rows = normalize_trend_payload(trend_data)
    body = "".join(
        "<tr>"
        f"<td>{escape(row['date'])}</td>"
        f"<td>{escape(row['tier'])}</td>"
        f"<td class=\"numeric\">{row['count']}</td>"
        "</tr>"
        for row in rows
    )
    return (
        '<table>'
        "<thead><tr><th>Date</th><th>Risk tier</th><th>Server count</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _dashboard_state(payload: Any = None) -> dict[str, Any]:
    rows = normalize_trend_payload(payload) if payload is not None else []
    return {
        "rows": rows,
        "figure": build_plotly_figure(rows),
        "summary": build_summary_statistics(rows),
        "has_data": bool(rows),
    }


def _render_dashboard_page(
    payload: Any = None,
    registry_counts: dict[str, int] | None = None,
) -> str:
    state = _dashboard_state(payload)
    registry_counts = registry_counts or {}
    registry_total = sum(registry_counts.values())
    registry_tiers = len(registry_counts)
    safe_state = json.dumps(state, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    table = build_html_table(state["rows"])
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Risk Tier Trend Dashboard</title>
<script src="{PLOTLY_URL}"></script>
<style>
:root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172033; background: #f4f6fa; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 32px 20px; }}
main {{ max-width: 1120px; margin: 0 auto; }}
header {{ margin-bottom: 24px; }}
h1 {{ margin: 0 0 8px; font-size: clamp(1.7rem, 3vw, 2.3rem); }}
.subtitle {{ margin: 0; color: #5c667a; }}
.panel, .stat {{ background: #fff; border: 1px solid #e1e6ef; border-radius: 12px; box-shadow: 0 2px 8px rgba(20, 33, 61, .04); }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 20px 0; }}
.stat {{ padding: 16px; }}
.stat-label {{ color: #687386; font-size: .82rem; }}
.stat-value {{ margin-top: 6px; font-size: 1.45rem; font-weight: 700; overflow-wrap: anywhere; }}
.panel {{ padding: 18px; margin: 16px 0; }}
.panel h2 {{ margin: 0 0 12px; font-size: 1.05rem; }}
#chart {{ width: 100%; min-height: 360px; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid #e7ebf2; text-align: left; }}
th {{ color: #586276; font-weight: 600; }}
.numeric {{ text-align: right; font-variant-numeric: tabular-nums; }}
#status {{ color: #596579; min-height: 1.4em; }}
#error {{ color: #a32632; }}
@media (max-width: 600px) {{ body {{ padding: 22px 12px; }} .panel {{ padding: 12px; }} #chart {{ min-height: 300px; }} }}
</style>
</head>
<body>
<main>
<header><h1>Risk Tier Trend</h1><p class="subtitle">Daily server counts by risk tier, with a current registry snapshot.</p></header>
<section class="stats" aria-label="Risk trend summary">
<div class="stat"><div class="stat-label">Trend dates</div><div class="stat-value" id="stat-observations">{state['summary']['observations']}</div></div>
<div class="stat"><div class="stat-label">Latest date</div><div class="stat-value" id="stat-latest-date">{escape(str(state['summary']['latest_date']))}</div></div>
<div class="stat"><div class="stat-label">Latest server count</div><div class="stat-value" id="stat-latest-total">{state['summary']['latest_total']}</div></div>
<div class="stat"><div class="stat-label">Average servers per date</div><div class="stat-value" id="stat-average-total">{state['summary']['average_total']}</div></div>
<div class="stat"><div class="stat-label">Current registry servers</div><div class="stat-value" id="stat-registry-total">{registry_total}</div></div>
<div class="stat"><div class="stat-label">Current registry tiers</div><div class="stat-value" id="stat-registry-tiers">{registry_tiers}</div></div>
</section>
<section class="panel" aria-labelledby="chart-title"><h2 id="chart-title">Servers by risk tier over time</h2><div id="chart"></div></section>
<section class="panel"><h2>Trend data</h2><p id="status" aria-live="polite">Loading risk trend…</p><p id="error" role="alert"></p><div class="table-wrap">{table}</div></section>
<script id="risk-tier-trend-initial" type="application/json">{safe_state}</script>
<script>
(() => {{
  const endpoint = {json.dumps(API_URL)};
  const initial = JSON.parse(document.getElementById("risk-tier-trend-initial").textContent);
  const statusNode = document.getElementById("status");
  const errorNode = document.getElementById("error");
  const tableBody = document.querySelector("table tbody");
  const chart = document.getElementById("chart");
  const dateText = value => {{
    if (value === null || value === undefined) return "";
    const text = String(value);
    return /^\\d{{4}}-\\d{{2}}-\\d{{2}}/.test(text) ? text.slice(0, 10) : text;
  }};
  const rowsFrom = value => {{
    if (Array.isArray(value)) return value;
    if (!value || typeof value !== "object") return [];
    for (const key of ["series", "trends", "trend", "risk_tier_trend", "points", "rows", "data"]) {{
      const nested = value[key];
      if (Array.isArray(nested)) return nested;
      if (nested && typeof nested === "object") {{
        const found = rowsFrom(nested);
        if (found.length) return found;
      }}
    }}
    return value.date || value.day || value.timestamp ? [value] : [];
  }};
  const normalize = payload => {{
    const totals = new Map();
    for (const row of rowsFrom(payload)) {{
      if (!row || typeof row !== "object") continue;
      const day = dateText(row.date || row.day || row.timestamp || row.observed_at);
      if (!day) continue;
      const tierCounts = row.tier_counts && typeof row.tier_counts === "object"
        ? row.tier_counts
        : row.tiers && typeof row.tiers === "object" ? row.tiers : null;
      const entries = tierCounts
        ? Object.entries(tierCounts)
        : [[row.tier || row.risk_tier || row.riskTier || row.label || "Unknown", row.count ?? row.server_count ?? row.total ?? row.score ?? 0]];
      for (const [tierValue, rawValue] of entries) {{
        const rawCount = rawValue && typeof rawValue === "object" ? rawValue.count ?? rawValue.value : rawValue;
        const count = Number(rawCount);
        if (!Number.isFinite(count) || count < 0 || !Number.isInteger(count)) continue;
        const tier = String(tierValue || "Unknown").trim() || "Unknown";
        const key = JSON.stringify([day, tier]);
        totals.set(key, (totals.get(key) || 0) + count);
      }}
    }}
    return Array.from(totals, ([key, count]) => {{
      const [date, tier] = JSON.parse(key);
      return {{ date, tier, count }};
    }}).sort((a, b) => a.date.localeCompare(b.date) || a.tier.localeCompare(b.tier));
  }};
  const makeFigure = rows => {{
    const dates = Array.from(new Set(rows.map(row => row.date))).sort();
    const tiers = Array.from(new Set(rows.map(row => row.tier))).sort((a, b) => a.localeCompare(b));
    const counts = new Map(rows.map(row => [JSON.stringify([row.tier, row.date]), row.count]));
    return {{
      data: tiers.map(tier => ({{
        type: "scatter", mode: "lines+markers", name: tier, x: dates,
        y: dates.map(day => counts.get(JSON.stringify([tier, day])) || 0)
      }})),
      layout: {{
        title: {{ text: "Risk Tier Trend" }},
        xaxis: {{ title: {{ text: "Date" }}, type: "date" }},
        yaxis: {{ title: {{ text: "Server Count" }}, rangemode: "tozero" }},
        hovermode: "x unified", margin: {{ t: 48, r: 24, b: 48, l: 56 }},
        legend: {{ orientation: "h", y: -0.2 }}
      }}
    }};
  }};
  const render = rows => {{
    const totals = new Map();
    for (const row of rows) totals.set(row.date, (totals.get(row.date) || 0) + row.count);
    const dates = Array.from(totals.keys()).sort();
    const latest = dates.length ? dates[dates.length - 1] : "—";
    const latestTotal = dates.length ? totals.get(latest) : 0;
    const average = dates.length ? Array.from(totals.values()).reduce((sum, n) => sum + n, 0) / dates.length : 0;
    document.getElementById("stat-observations").textContent = String(dates.length);
    document.getElementById("stat-latest-date").textContent = latest;
    document.getElementById("stat-latest-total").textContent = String(latestTotal);
    document.getElementById("stat-average-total").textContent = average.toFixed(2);
    tableBody.replaceChildren();
    for (const row of rows) {{
      const tr = document.createElement("tr");
      for (const value of [row.date, row.tier, String(row.count)]) {{
        const td = document.createElement("td");
        td.textContent = value;
        if (typeof value === "string" && /^\\d+$/.test(value)) td.className = "numeric";
        tr.appendChild(td);
      }}
      tableBody.appendChild(tr);
    }}
    if (window.Plotly && typeof window.Plotly.newPlot === "function") {{
      const figure = makeFigure(rows);
      window.Plotly.newPlot(chart, figure.data, figure.layout, {{ responsive: true, displaylogo: false }});
    }} else {{
      chart.textContent = "Chart library is unavailable; the data table remains available.";
    }}
    statusNode.textContent = rows.length ? `${{rows.length}} risk-tier records across ${{dates.length}} dates.` : "No risk-trend records are available.";
  }};
  const load = async () => {{
    if (initial.has_data) render(initial.rows);
    try {{
      const response = await fetch(endpoint, {{ headers: {{ Accept: "application/json" }} }});
      if (!response.ok) throw new Error(`Risk trend request failed (${{response.status}}).`);
      render(normalize(await response.json()));
      errorNode.textContent = "";
    }} catch (error) {{
      errorNode.textContent = error instanceof Error ? error.message : "Unable to load risk trend data.";
      if (!initial.has_data) statusNode.textContent = "Risk trend data could not be loaded.";
    }}
  }};
  document.addEventListener("DOMContentLoaded", load);
}})();
</script>
</main>
</body>
</html>'''


async def get_dashboard_html(session: Session = Depends(get_session)) -> str:
    registry_counts = (
        risk_tier_histogram(session)
        if callable(getattr(session, "execute", None))
        else {}
    )
    return _render_dashboard_page(registry_counts=registry_counts)


if __name__ == "__main__":
    mock_api_response = {
        "data": [
            {"date": "2026-09-24", "tier_counts": {"LOW": 8, "HIGH": 2}},
            {"date": "2026-09-25", "tier_counts": {"LOW": 7, "HIGH": 3}},
            {"date": "2026-09-26", "tier_counts": {"LOW": 6, "HIGH": 4}},
        ],
        "summary": {"days": 3},
    }
    state = _dashboard_state(mock_api_response)
    page = _render_dashboard_page(mock_api_response, {"HIGH": 4, "LOW": 6})
    assert state["has_data"]
    assert len(state["figure"]["data"]) == 2
    assert all(trace["type"] == "scatter" and trace["mode"] == "lines+markers" for trace in state["figure"]["data"])
    assert state["summary"]["observations"] == 3
    assert state["summary"]["latest_total"] == 10
    assert '<div id="chart"' in page
    assert '<table>' in page
    assert "2026-09-26" in page and "LOW" in page and ">6</td>" in page
    assert "fetch(endpoint" in page and API_URL in page
    print("PASS")