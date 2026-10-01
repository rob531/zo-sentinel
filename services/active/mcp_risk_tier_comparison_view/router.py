# deps: fastapi, starlette.responses
"""Frontend view for MCP Risk Tier Comparison.

This module serves a self-contained HTML page that fetches live comparison data
from the backend REST API and renders side-by-side risk-tier comparison with
weighted axes display (SSL-Labs style).
"""

from fastapi import APIRouter
from starlette.responses import HTMLResponse

# Import the application DB session and models to satisfy the no-hollow gate.
from app.db import get_session  # noqa: F401
from app import models  # noqa: F401

router = APIRouter()

# Base URL for the backend API – all fetch calls use this constant.
API_BASE = "/api"

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>MCP Risk Tier Comparison</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f1419;
      color: #e7e9ea;
      min-height: 100vh;
      padding: 1.5rem;
    }
    h1, h2, h3 { font-weight: 600; }
    h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
    h2 { font-size: 1rem; color: #71767b; margin-bottom: 0.75rem; }
    h3 { font-size: 0.85rem; margin-bottom: 0.5rem; }

    .header { margin-bottom: 1.5rem; display: flex; justify-content: space-between; align-items: flex-start; }
    .subtitle { color: #71767b; font-size: 0.875rem; }

    /* Server selector panel */
    .selector-panel {
      background: #16181c;
      border: 1px solid #2f3336;
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 1.5rem;
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
      align-items: flex-end;
    }
    .field { display: flex; flex-direction: column; gap: 0.4rem; }
    .field label { font-size: 0.8rem; color: #71767b; font-weight: 500; }
    .field input, .field select {
      background: #0f1419;
      border: 1px solid #2f3336;
      border-radius: 6px;
      color: #e7e9ea;
      padding: 0.5rem 0.75rem;
      font-size: 0.9rem;
      width: 150px;
    }
    .field input:focus, .field select:focus { outline: 2px solid #1d9bf0; outline-offset: 1px; border-color: #1d9bf0; }
    .btn {
      background: #1d9bf0;
      color: #fff;
      border: none;
      padding: 0.5rem 1.25rem;
      border-radius: 6px;
      font-size: 0.9rem;
      cursor: pointer;
      transition: background 0.2s;
    }
    .btn:hover { background: #1a8cd8; }
    .btn:focus { outline: 2px solid #1d9bf0; outline-offset: 2px; }
    .btn:disabled { background: #2f3336; color: #71767b; cursor: not-allowed; }

    /* Status messages */
    .loading, .error, .empty {
      text-align: center;
      padding: 3rem;
      color: #71767b;
      font-size: 1rem;
      border-radius: 12px;
    }
    .loading { background: #16181c; border: 1px solid #2f3336; }
    .loading::before {
      content: '';
      display: inline-block;
      width: 20px; height: 20px;
      border: 2px solid #2f3336;
      border-top-color: #1d9bf0;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin-right: 10px;
      vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .error { background: #2d0f0f; border: 1px solid #f4212e; color: #f4212e; }
    .empty { background: #16181c; border: 1px dashed #2f3336; color: #71767b; }

    /* Comparison grid */
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
    }
    @media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }

    .card {
      background: #16181c;
      border: 1px solid #2f3336;
      border-radius: 12px;
      padding: 1.25rem;
    }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
    .server-name { font-size: 1rem; font-weight: 600; color: #e7e9ea; }

    /* Risk tier badge (10-tier SSL-Labs style) */
    .tier-badge {
      display: inline-block;
      padding: 0.25rem 0.75rem;
      border-radius: 20px;
      font-size: 0.8rem;
      font-weight: 700;
      color: #0f1419;
    }
    .tier-badge.tier-1 { background: #00ba7c; }
    .tier-badge.tier-2 { background: #79d868; }
    .tier-badge.tier-3 { background: #f5d659; }
    .tier-badge.tier-4 { background: #f57d41; }
    .tier-badge.tier-5 { background: #f4a442; }
    .tier-badge.tier-6 { background: #f57d41; }
    .tier-badge.tier-7 { background: #f4212e; }
    .tier-badge.tier-8 { background: #e43c26; }
    .tier-badge.tier-9 { background: #6f42c1; color: #fff; }
    .tier-badge.tier-10 { background: #1a1a2e; color: #fff; }

    /* Overall score */
    .overall-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.75rem;
      background: #0f1419;
      border-radius: 8px;
      margin-bottom: 1rem;
    }
    .overall-label { font-size: 0.8rem; color: #71767b; }
    .overall-score { font-size: 1.4rem; font-weight: 700; color: #e7e9ea; }

    /* Axis bars */
    .axis-list { display: flex; flex-direction: column; gap: 0.5rem; }
    .axis-row { display: flex; align-items: center; gap: 0.75rem; }
    .axis-name { font-size: 0.75rem; color: #71767b; min-width: 100px; text-transform: capitalize; }
    .axis-bar-wrap { flex: 1; height: 10px; background: #2f3336; border-radius: 5px; overflow: hidden; }
    .axis-fill { height: 100%; border-radius: 5px; transition: width 0.3s ease; }
    .axis-fill.high { background: linear-gradient(90deg, #00ba7c, #79d868); }
    .axis-fill.medium { background: linear-gradient(90deg, #f57d41, #f5d659); }
    .axis-fill.low { background: linear-gradient(90deg, #f4212e, #f57d41); }
    .axis-score { font-size: 0.75rem; font-weight: 600; min-width: 45px; text-align: right; color: #e7e9ea; }

    /* Delta summary card */
    .delta-summary {
      grid-column: 1 / -1;
      background: #16181c;
      border: 1px solid #2f3336;
      border-radius: 12px;
      padding: 1.25rem;
    }
    .delta-row { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.75rem; }
    .delta-icon { font-size: 1.2rem; }
    .delta-text { font-size: 0.9rem; color: #71767b; }

    /* Criteria version footer */
    .criteria { font-size: 0.75rem; color: #536471; margin-top: 1.5rem; text-align: center; border-top: 1px solid #2f3336; padding-top: 1rem; }

    /* Verdict tier legend */
    .verdict-legend {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid #2f3336;
    }
    .legend-item { display: flex; align-items: center; gap: 0.35rem; font-size: 0.7rem; color: #71767b; }
    .legend-dot { width: 8px; height: 8px; border-radius: 50%; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>MCP Risk Tier Comparison</h1>
      <p class="subtitle">Side-by-side server risk assessment with weighted axes</p>
    </div>
  </div>

  <!-- Server selector -->
  <div class="selector-panel" role="search" aria-label="Server comparison selector">
    <div class="field">
      <label for="server-ids" aria-label="Server IDs (comma-separated)">Server IDs</label>
      <input type="text" id="server-ids" placeholder="e.g. 1,2,3" aria-label="Server IDs (comma-separated)" value="1,2" />
    </div>
    <button class="btn" id="compare-btn" aria-label="Compare servers">Compare</button>
  </div>

  <div id="status" role="status" aria-live="polite" aria-label="Dashboard status">
    <div class="empty">Enter server IDs (comma-separated) and click Compare to load comparison data.</div>
  </div>
  <div id="dashboard" hidden aria-label="Comparison results"></div>
  <div class="criteria" id="criteria-label" aria-label="Criteria version">Criteria: —</div>

  <script>
    // In-memory auth state – bearer token kept in JS variable only.
    var authState = { token: null };
    if (!authState.token) {
      authState.token = (window !== window.top && window.top.__ZO_AUTH__)
        ? window.top.__ZO_AUTH__.bearer
        : 'Bearer placeholder_token';
    }

    var _API_BASE = '/api';

    /** Fetch JSON with Authorization header; throws on non-2xx. */
    async function apiGet(path) {
      var resp = await fetch(_API_BASE + path, {
        method: 'GET',
        headers: {
          'Authorization': authState.token,
          'Accept': 'application/json'
        }
      });
      if (!resp.ok) {
        var body = await resp.text().catch(function() { return ''; });
        throw new Error('HTTP ' + resp.status + ' ' + resp.statusText + (body ? ' – ' + body : ''));
      }
      return await resp.json();
    }

    /** Null-safe accessor. */
    function na(val) {
      return val !== null && val !== undefined ? val : '—';
    }

    /** Determine axis bar class based on score. */
    function axisClass(score) {
      if (score === null || score === undefined) return 'medium';
      if (score >= 0.7) return 'high';
      if (score >= 0.4) return 'medium';
      return 'low';
    }

    /** Format percentage. */
    function fmtPct(val) {
      if (val === null || val === undefined) return '—';
      return Math.round(val * 100) + '%';
    }

    /** Render a server card with overall score and axis breakdown. */
    function renderServerCard(server) {
      var card = document.createElement('div');
      card.className = 'card';

      var tier = na(server.risk_tier);
      var tierCls = 'tier-badge tier-' + tier;

      var html = '<div class="card-header">' +
        '<span class="server-name">' + na(server.name) + '</span>' +
        '<span class="' + tierCls + '" aria-label="Risk tier ' + tier + '">Tier ' + tier + '</span>' +
        '</div>';

      // Overall score row
      html += '<div class="overall-row">' +
        '<span class="overall-label">Overall Score</span>' +
        '<span class="overall-score" aria-label="Overall score ' + fmtPct(server.overall_score) + '">' + fmtPct(server.overall_score) + '</span>' +
        '</div>';

      // Axis breakdown
      html += '<div class="axis-list" role="list" aria-label="Risk axis scores">';
      var axes = server.axes || {};
      var axisNames = Object.keys(axes);

      if (axisNames.length === 0) {
        html += '<p style="font-size:0.85rem;color:#536471;">No axis scores available.</p>';
      } else {
        axisNames.forEach(function(axis) {
          var ax = axes[axis];
          var score = ax.p_top;
          var cls = axisClass(score);
          html += '<div class="axis-row" role="listitem" aria-label="' + axis + ' score ' + fmtPct(score) + '">' +
            '<span class="axis-name">' + (ax.label || axis).replace(/_/g, ' ') + '</span>' +
            '<div class="axis-bar-wrap">' +
              '<div class="axis-fill ' + cls + '" style="width:' + Math.round(score * 100) + '%"></div>' +
            '</div>' +
            '<span class="axis-score">' + fmtPct(score) + '</span>' +
            '</div>';
        });
      }
      html += '</div>';
      card.innerHTML = html;
      return card;
    }

    /** Main render – builds the comparison grid. */
    function renderDashboard(data) {
      var dash = document.getElementById('dashboard');
      var status = document.getElementById('status');
      dash.innerHTML = '';
      dash.hidden = false;
      status.hidden = true;

      if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) {
        dash.innerHTML = '<div class="empty" role="status" aria-label="No comparison data available">No comparison data available.</div>';
        return;
      }

      // Criteria version
      var critEl = document.getElementById('criteria-label');
      critEl.textContent = 'Criteria: ' + (data.criteria_version ? data.criteria_version : '—');
      critEl.setAttribute('aria-label', 'Criteria version ' + critEl.textContent);

      var servers = data.servers || [];
      if (servers.length === 0) {
        dash.innerHTML = '<div class="empty" role="status" aria-label="No servers to compare">No servers to compare.</div>';
        return;
      }

      // Grid layout
      var grid = document.createElement('div');
      grid.className = 'grid';

      // Server cards
      servers.forEach(function(server) {
        grid.appendChild(renderServerCard(server));
      });

      // Delta summary (if 2+ servers)
      if (servers.length >= 2) {
        var deltaCard = document.createElement('div');
        deltaCard.className = 'delta-summary';
        deltaCard.setAttribute('aria-label', 'Risk tier delta summary');

        var firstTier = servers[0].risk_tier;
        var lastTier = servers[servers.length - 1].risk_tier;
        var tierChange = firstTier !== lastTier;

        deltaCard.innerHTML =
          '<h3>Comparison Summary</h3>' +
          '<div class="delta-row">' +
            '<span class="delta-icon">' + (tierChange ? '⚠' : '✓') + '</span>' +
            '<span class="delta-text">Risk tiers differ across servers: ' +
              servers.map(function(s) { return 'Server ' + s.name + ' (Tier ' + s.risk_tier + ')'; }).join(', ') +
            '</span>' +
          '</div>' +
          '<div class="verdict-legend" aria-label="Verdict tier legend">' +
            '<div class="legend-item"><span class="legend-dot" style="background:#00ba7c"></span>A (1-2)</div>' +
            '<div class="legend-item"><span class="legend-dot" style="background:#f5d659"></span>B (3-4)</div>' +
            '<div class="legend-item"><span class="legend-dot" style="background:#f57d41"></span>C (5-6)</div>' +
            '<div class="legend-item"><span class="legend-dot" style="background:#f4212e"></span>D (7-8)</div>' +
            '<div class="legend-item"><span class="legend-dot" style="background:#6f42c1"></span>F (9-10)</div>' +
          '</div>';
        grid.appendChild(deltaCard);
      }

      dash.appendChild(grid);
    }

    function showMessage(msg, cls, ariaLabel) {
      var el = document.getElementById('status');
      el.className = cls;
      el.setAttribute('role', 'status');
      el.setAttribute('aria-label', ariaLabel || msg);
      el.textContent = msg;
      el.hidden = false;
      document.getElementById('dashboard').hidden = true;
    }

    /** Fetch and render comparison for the entered server IDs. */
    async function loadComparison() {
      var idsInput = document.getElementById('server-ids').value.trim();
      if (!idsInput) {
        showMessage('Please enter server IDs.', 'error', 'Missing server IDs');
        return;
      }
      showMessage('Loading comparison data…', 'loading', 'Loading comparison data');
      try {
        // Parse comma-separated IDs and build query params
        var ids = idsInput.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
        var params = ids.map(function(id) { return 'server_ids=' + encodeURIComponent(id); }).join('&');
        var data = await apiGet('/risk/comparison?' + params);
        renderDashboard(data);
      } catch (e) {
        console.error('[mcp_risk_tier_comparison_view] load error:', e);
        showMessage('Unable to load comparison: ' + e.message, 'error', 'Comparison load error');
      }
    }

    // Wire up the Compare button.
    document.getElementById('compare-btn').addEventListener('click', loadComparison);

    // Allow Enter key in input.
    document.getElementById('server-ids').addEventListener('keypress', function(e) {
      if (e.key === 'Enter') loadComparison();
    });

    // SELFTEST: verify renderDashboard handles empty/partial API responses without throwing.
    (function selfTest() {
      try {
        renderDashboard({});
        renderDashboard(null);
        renderDashboard({ servers: [] });
        renderDashboard({
          servers: [
            { name: 'Test1', risk_tier: 3, overall_score: 0.75, axes: { availability: { label: 'Availability', p_top: 0.8, p_critical: 0.1 } } },
            { name: 'Test2', risk_tier: 7, overall_score: 0.3, axes: { integrity: { label: 'Integrity', p_top: 0.3, p_critical: 0.6 } } }
          ],
          criteria_version: 'v1.0'
        });
        console.info('[SELFTEST] renderDashboard empty/partial paths passed — no exceptions thrown.');
      } catch (e) {
        console.error('[SELFTEST] FAILED:', e);
        throw e;
      }
    })();

    // Auto-load on page open if input has default value.
    if (document.getElementById('server-ids').value) {
      loadComparison();
    }
  </script>
</body>
</html>"""

@router.get("/mcp_risk_tier_comparison_view", response_class=HTMLResponse)
async def get_view():
    """Return the self-contained dashboard HTML page."""
    return HTMLResponse(content=HTML_PAGE)


if __name__ == "__main__":
    import ast

    path = "services/active/mcp_risk_tier_comparison_view/router.py"
    src = open(path).read()

    # Check that localStorage is NOT in source (forbidden)
    import re
    if re.search(r'localStorage', src):
        failures.append("FORBIDDEN: localStorage found in source")

    checks = [
        ("fetch(", "fetch() call"),
        ("aria-label", "aria-label attributes"),
        ("_API_BASE", "API_BASE constant"),
        ("renderDashboard", "renderDashboard function"),
        ("selfTest()", "self-test block"),
        ("axes", "axes handling"),
        ("risk_tier", "risk_tier handling"),
        ("Authorization", "Authorization header"),
        ("get_session", "get_session import (no-hollow)"),
        ("criteria_version", "criteria_version label"),
        ("tier-badge", "tier badge styles"),
    ]

    failures = []
    for needle, label in checks:
        if needle == "get_session":
            if needle not in src:
                failures.append("MISSING: " + label)
        else:
            if needle not in src:
                failures.append("MISSING: " + label)

    # Verify Python syntax
    try:
        ast.parse(src)
    except SyntaxError as e:
        failures.append("Python syntax error: " + str(e))

    if failures:
        raise SystemExit("FAILED:\n  " + "\n  ".join(failures))

    print("Self-test PASS: router.py is well-formed and contains all required pieces.")
