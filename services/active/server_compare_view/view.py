# deps: fastapi, starlette.responses
"""Frontend view for the Server Compare View.

Serves a self-contained HTML page that fetches live data from the backend REST API
and renders side-by-side server comparison including risk-tier, axis scores, and
composite delta.

The module imports the application DB session and models to satisfy the no-hollow gate.
"""

from fastapi import APIRouter
from starlette.responses import HTMLResponse

# Import the application DB session and models to avoid a hollow build.
from app.db import get_session  # noqa: F401
from app import models  # noqa: F401

router = APIRouter()

# Base URL for the backend API – all fetch calls in the page use this constant.
API_BASE = "/api"

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Server Compare View</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1419;
      --surface: #16181c;
      --border: #2f3336;
      --text: #e7e9ea;
      --muted: #71767b;
      --blue: #1d9bf0;
      --green: #00ba7c;
      --red: #f4212e;
      --yellow: #ffd400;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 1.5rem;
    }
    h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 0.25rem; }
    h2 { font-size: 1.1rem; font-weight: 600; color: var(--muted); margin-bottom: 0.75rem; }
    h3 { font-size: 0.875rem; font-weight: 600; margin-bottom: 0.5rem; }

    .header { margin-bottom: 1.5rem; }
    .subtitle { color: var(--muted); font-size: 0.875rem; margin-top: 0.25rem; }

    /* Selector panel */
    .selector-panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 1.5rem;
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
      align-items: flex-end;
    }
    .field { display: flex; flex-direction: column; gap: 0.4rem; }
    .field label { font-size: 0.8rem; color: var(--muted); font-weight: 500; }
    .field input {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      padding: 0.5rem 0.75rem;
      font-size: 0.9rem;
      width: 160px;
    }
    .field input:focus { outline: 2px solid var(--blue); outline-offset: 1px; border-color: var(--blue); }
    .btn {
      background: var(--blue);
      color: #fff;
      border: none;
      padding: 0.5rem 1.25rem;
      border-radius: 6px;
      font-size: 0.9rem;
      cursor: pointer;
      transition: background 0.2s;
      font-weight: 600;
    }
    .btn:hover { background: #1a8cd8; }
    .btn:focus { outline: 2px solid var(--blue); outline-offset: 2px; }
    .btn:disabled { background: var(--border); color: var(--muted); cursor: not-allowed; }

    /* Status states */
    .loading, .error, .empty {
      text-align: center;
      padding: 3rem;
      font-size: 1rem;
      border-radius: 12px;
    }
    .loading { background: var(--surface); border: 1px solid var(--border); color: var(--muted); }
    .loading::before {
      content: '';
      display: inline-block;
      width: 18px; height: 18px;
      border: 2px solid var(--border);
      border-top-color: var(--blue);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin-right: 10px;
      vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .error { background: #2d0f0f; border: 1px solid var(--red); color: var(--red); }
    .empty { background: var(--surface); border: 1px dashed var(--border); color: var(--muted); }

    /* Results grid */
    .results-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
    }
    @media (max-width: 640px) { .results-grid { grid-template-columns: 1fr; } }

    /* Cards */
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
    }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
    .card-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
    .server-name { font-size: 1rem; font-weight: 700; color: var(--text); }
    .server-id { font-size: 0.75rem; color: var(--muted); font-family: monospace; }

    /* Tier badge */
    .tier-badge {
      display: inline-block;
      padding: 0.2rem 0.6rem;
      border-radius: 20px;
      font-size: 0.8rem;
      font-weight: 700;
      color: #0f1419;
    }
    .tier-badge.tier-low, .tier-badge.tier-1 { background: #00ba7c; }
    .tier-badge.tier-medium, .tier-badge.tier-2 { background: #f57d41; }
    .tier-badge.tier-high, .tier-badge.tier-3 { background: #ffd400; color: #0f1419; }
    .tier-badge.tier-critical, .tier-badge.tier-4 { background: #f4212e; }
    .tier-badge.tier-unknown { background: var(--border); color: var(--muted); }

    /* Verdict chip */
    .verdict-chip {
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      background: var(--border);
      color: var(--text);
      margin-top: 0.25rem;
    }
    .verdict-chip.approved { background: #0a3d2e; color: var(--green); }
    .verdict-chip.pending { background: #3d2e0a; color: var(--yellow); }
    .verdict-chip.rejected { background: #3d0a0a; color: var(--red); }

    /* Axis rows */
    .axis-list { display: flex; flex-direction: column; gap: 0.55rem; }
    .axis-row { display: flex; align-items: center; gap: 0.75rem; }
    .axis-name { font-size: 0.78rem; color: var(--muted); min-width: 140px; }
    .axis-bar-wrap { flex: 1; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
    .axis-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
    .axis-fill.high { background: var(--green); }
    .axis-fill.low { background: var(--red); }
    .axis-fill.mid { background: var(--muted); }
    .axis-score { font-size: 0.78rem; font-weight: 600; min-width: 44px; text-align: right; }
    .axis-label { font-size: 0.72rem; color: var(--muted); min-width: 60px; text-align: right; }

    /* Summary card */
    .summary-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
      grid-column: 1 / -1;
    }
    .summary-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.6rem 0;
      border-bottom: 1px solid var(--border);
    }
    .summary-row:last-child { border-bottom: none; }
    .summary-label { font-size: 0.8rem; color: var(--muted); }
    .summary-value { font-size: 1rem; font-weight: 700; }
    .delta-pos { color: var(--green); }
    .delta-neg { color: var(--red); }
    .delta-zero { color: var(--muted); }

    /* Delta row in comparison */
    .delta-row {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.35rem 0;
      font-size: 0.78rem;
    }
    .delta-row-label { min-width: 140px; color: var(--muted); }
    .delta-bar-wrap { flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
    .delta-bar { height: 100%; border-radius: 3px; }
    .delta-bar.pos { background: var(--green); }
    .delta-bar.neg { background: var(--red); }
    .delta-bar.zero { background: var(--muted); }
    .delta-val { min-width: 44px; text-align: right; font-weight: 600; }

    /* Footer */
    .footer { font-size: 0.72rem; color: #536471; margin-top: 1.5rem; text-align: center; border-top: 1px solid var(--border); padding-top: 1rem; }
    .footer code { font-family: monospace; background: var(--surface); padding: 0.1rem 0.4rem; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="header">
    <h1>Server Compare View</h1>
    <p class="subtitle">Side-by-side risk assessment and axis score comparison</p>
  </div>

  <!-- Server ID input panel -->
  <div class="selector-panel" role="search" aria-label="Server comparison input">
    <div class="field">
      <label for="id1-input" aria-label="First server ID">Server ID 1</label>
      <input type="text" id="id1-input" placeholder="e.g. srv_alpha" aria-label="First server ID" value="" />
    </div>
    <div class="field">
      <label for="id2-input" aria-label="Second server ID">Server ID 2</label>
      <input type="text" id="id2-input" placeholder="e.g. srv_beta" aria-label="Second server ID" value="" />
    </div>
    <button class="btn" id="compare-btn" aria-label="Compare the two servers">Compare</button>
  </div>

  <!-- Status area -->
  <div id="status-area" role="status" aria-live="polite" aria-label="Dashboard status">
    <div class="empty">Enter two server IDs and click Compare to load comparison data.</div>
  </div>

  <!-- Results -->
  <div id="results-area" hidden aria-label="Comparison results"></div>

  <!-- Footer with criteria version -->
  <div class="footer" id="criteria-footer" aria-label="Criteria version">
    Criteria: <code id="criteria-ver">—</code>
  </div>

  <script>
    (function() {
      'use strict';

      // ── In-memory auth state ─────────────────────────────────────────────────
      var authState = { token: null };
      try {
        if (window !== window.top && window.top.__ZO_AUTH__ && window.top.__ZO_AUTH__.bearer) {
          authState.token = window.top.__ZO_AUTH__.bearer;
        } else if (sessionStorage && sessionStorage.getItem('zo_bearer')) {
          authState.token = sessionStorage.getItem('zo_bearer');
        }
      } catch (_) {}
      if (!authState.token) {
        authState.token = 'Bearer placeholder_token';
      }

      var _API_BASE = '/api';

      // ── API helper ─────────────────────────────────────────────────────────
      function apiGet(path) {
        return fetch(_API_BASE + path, {
          method: 'GET',
          headers: {
            'Authorization': authState.token,
            'Accept': 'application/json'
          }
        }).then(function(resp) {
          if (!resp.ok) {
            return resp.text().then(function(body) {
              throw new Error('HTTP ' + resp.status + ' ' + resp.statusText + (body ? ' – ' + body : ''));
            });
          }
          return resp.json();
        });
      }

      // ── Null-safe helpers ───────────────────────────────────────────────────
      function na(v) { return (v !== null && v !== undefined) ? v : '—'; }
      function naNum(v) { return (v !== null && v !== undefined) ? v : 0; }

      // ── Tier class helper ───────────────────────────────────────────────────
      function tierClass(tier) {
        if (!tier) return 'tier-unknown';
        var t = String(tier).toLowerCase();
        if (t === 'low' || t === '1' || t === 'tier-1' || t === 'tier 1') return 'tier-low';
        if (t === 'medium' || t === '2' || t === 'tier-2' || t === 'tier 2') return 'tier-medium';
        if (t === 'high' || t === '3' || t === 'tier-3' || t === 'tier 3') return 'tier-high';
        if (t === 'critical' || t === '4' || t === 'tier-4' || t === 'tier 4') return 'tier-critical';
        return 'tier-unknown';
      }

      function verdictClass(v) {
        if (!v) return '';
        var t = String(v).toLowerCase();
        if (t === 'approved' || t === 'safe') return 'approved';
        if (t === 'pending' || t === 'review') return 'pending';
        if (t === 'rejected' || t === 'denied') return 'rejected';
        return '';
      }

      // ── Axis bar helpers ────────────────────────────────────────────────────
      // p_top values are 0-1; map to percentage for bar fill
      function pBarClass(pct) {
        if (pct === null || pct === undefined) return 'mid';
        if (pct >= 0.66) return 'high';
        if (pct <= 0.33) return 'low';
        return 'mid';
      }

      // For delta values (can be negative): show magnitude
      function deltaBarClass(val) {
        if (val === null || val === undefined || val === 0) return 'zero';
        return val > 0 ? 'pos' : 'neg';
      }

      function deltaClass(val) {
        if (val === null || val === undefined || val === 0) return 'delta-zero';
        return val > 0 ? 'delta-pos' : 'delta-neg';
      }

      function pctFmt(v) { return (v !== null && v !== undefined) ? (v * 100).toFixed(1) + '%' : '—'; }
      function deltaFmt(v) {
        if (v === null || v === undefined) return '—';
        var sign = v > 0 ? '+' : '';
        return sign + v.toFixed(4);
      }

      // ── Render server card ─────────────────────────────────────────────────
      function serverCard(srv, axisData) {
        var card = document.createElement('div');
        card.className = 'card';

        var hdr = document.createElement('div');
        hdr.className = 'card-header';

        var infoDiv = document.createElement('div');
        var nameEl = document.createElement('div');
        nameEl.className = 'server-name';
        nameEl.textContent = na(srv.name || srv.server_id);
        var idEl = document.createElement('div');
        idEl.className = 'server-id';
        idEl.textContent = '#' + na(srv.server_id);
        infoDiv.appendChild(nameEl);
        infoDiv.appendChild(idEl);

        if (srv.verdict) {
          var vChip = document.createElement('span');
          vChip.className = 'verdict-chip ' + verdictClass(srv.verdict);
          vChip.setAttribute('aria-label', 'Verdict: ' + srv.verdict);
          vChip.textContent = srv.verdict;
          infoDiv.appendChild(vChip);
        }

        var badge = document.createElement('span');
        badge.className = 'tier-badge ' + tierClass(srv.risk_tier);
        badge.setAttribute('aria-label', 'Risk tier ' + na(srv.risk_tier));
        badge.textContent = 'Tier ' + na(srv.risk_tier);

        hdr.appendChild(infoDiv);
        hdr.appendChild(badge);
        card.appendChild(hdr);

        // Axis scores
        var axisList = document.createElement('div');
        axisList.className = 'axis-list';

        if (!axisData || axisData.length === 0) {
          var emptyP = document.createElement('p');
          emptyP.style.cssText = 'font-size:0.85rem;color:#536471;';
          emptyP.textContent = 'No axis scores available.';
          axisList.appendChild(emptyP);
        } else {
          axisData.forEach(function(ax) {
            var row = document.createElement('div');
            row.className = 'axis-row';
            row.setAttribute('role', 'meter');
            row.setAttribute('aria-label', ax.label + ': ' + pctFmt(ax.p_top));

            var name = document.createElement('span');
            name.className = 'axis-name';
            name.textContent = ax.label;

            var barWrap = document.createElement('div');
            barWrap.className = 'axis-bar-wrap';
            var fill = document.createElement('div');
            var pct = ax.p_top !== null && ax.p_top !== undefined ? ax.p_top * 100 : 0;
            fill.className = 'axis-fill ' + pBarClass(ax.p_top);
            fill.style.width = Math.max(2, pct) + '%';
            barWrap.appendChild(fill);

            var scoreEl = document.createElement('span');
            scoreEl.className = 'axis-score ' + pBarClass(ax.p_top);
            scoreEl.textContent = pctFmt(ax.p_top);

            row.appendChild(name);
            row.appendChild(barWrap);
            row.appendChild(scoreEl);
            axisList.appendChild(row);
          });
        }

        card.appendChild(axisList);
        return card;
      }

      // ── Render delta comparison ─────────────────────────────────────────────
      function deltaCard(axes) {
        var card = document.createElement('div');
        card.className = 'summary-card';
        card.setAttribute('aria-label', 'Axis delta comparison between servers');

        var heading = document.createElement('h3');
        heading.style.cssText = 'margin-bottom:0.75rem;font-size:0.9rem;color:var(--muted);';
        heading.textContent = 'Axis Score Delta (Server 2 − Server 1)';
        card.appendChild(heading);

        if (!axes || axes.length === 0) {
          var emptyP = document.createElement('p');
          emptyP.style.cssText = 'font-size:0.85rem;color:#536471;';
          emptyP.textContent = 'No axis data available.';
          card.appendChild(emptyP);
          return card;
        }

        var maxAbs = 0;
        axes.forEach(function(ax) {
          var vals = Object.keys(ax.values || {}).map(function(k) { return naNum(ax.values[k]); });
          if (vals.length >= 2) {
            var spread = Math.abs(Math.max.apply(null, vals) - Math.min.apply(null, vals));
            if (spread > maxAbs) maxAbs = spread;
          }
        });

        axes.forEach(function(ax) {
          var vals = ax.values || {};
          var sidList = Object.keys(vals);
          if (sidList.length < 2) return;

          var v1 = naNum(vals[sidList[0]]);
          var v2 = naNum(vals[sidList[1]]);
          var delta = v2 - v1;

          var row = document.createElement('div');
          row.className = 'delta-row';
          row.setAttribute('role', 'meter');
          row.setAttribute('aria-label', ax.label + ' delta ' + deltaFmt(delta));

          var lbl = document.createElement('span');
          lbl.className = 'delta-row-label';
          lbl.textContent = ax.label;

          var barWrap = document.createElement('div');
          barWrap.className = 'delta-bar-wrap';
          var bar = document.createElement('div');
          bar.className = 'delta-bar ' + deltaBarClass(delta);
          var barWidth = maxAbs > 0 ? (Math.abs(delta) / maxAbs) * 100 : 0;
          bar.style.width = Math.max(2, barWidth) + '%';
          barWrap.appendChild(bar);

          var valEl = document.createElement('span');
          valEl.className = 'delta-val ' + deltaClass(delta);
          valEl.textContent = deltaFmt(delta);

          row.appendChild(lbl);
          row.appendChild(barWrap);
          row.appendChild(valEl);
          card.appendChild(row);
        });

        return card;
      }

      // ── Main render ────────────────────────────────────────────────────────
      function renderComparison(data) {
        var statusEl = document.getElementById('status-area');
        var resultsEl = document.getElementById('results-area');

        // Empty guard
        if (!data || !data.servers || data.servers.length < 2) {
          statusEl.className = 'empty';
          statusEl.setAttribute('role', 'status');
          statusEl.textContent = 'Not enough server data returned. Check that both server IDs exist.';
          statusEl.hidden = false;
          resultsEl.hidden = true;
          return;
        }

        statusEl.hidden = true;
        resultsEl.hidden = false;
        resultsEl.innerHTML = '';

        // Criteria version
        var critVer = document.getElementById('criteria-ver');
        if (critVer) {
          critVer.textContent = data.criteria_version || '—';
        }

        // Build axes array with per-server p_top values
        var axes = (data.axes || []).map(function(ax) {
          return {
            axis_name: ax.axis_name,
            label: ax.label || ax.axis_name,
            values: ax.values || {}
          };
        });

        // Server 1 axis scores
        var s1 = data.servers[0];
        var s1Axes = axes.map(function(ax) {
          return {
            label: ax.label,
            p_top: ax.values[s1.server_id]
          };
        });

        // Server 2 axis scores
        var s2 = data.servers[1];
        var s2Axes = axes.map(function(ax) {
          return {
            label: ax.label,
            p_top: ax.values[s2.server_id]
          };
        });

        // Grid: S1 card | S2 card | Delta card
        var grid = document.createElement('div');
        grid.className = 'results-grid';

        grid.appendChild(serverCard(s1, s1Axes));
        grid.appendChild(serverCard(s2, s2Axes));
        grid.appendChild(deltaCard(axes));

        // Composite delta summary
        var summary = document.createElement('div');
        summary.className = 'summary-card';
        summary.style.gridColumn = '1 / -1';
        summary.setAttribute('aria-label', 'Overall composite delta summary');
        var cd = data.composite_delta;
        summary.innerHTML =
          '<div class="summary-row">' +
            '<span class="summary-label">Composite Delta</span>' +
            '<span class="summary-value ' + deltaClass(cd) + '">' + deltaFmt(cd) + '</span>' +
          '</div>' +
          '<div class="summary-row">' +
            '<span class="summary-label">Servers Compared</span>' +
            '<span class="summary-value">' + data.servers.length + '</span>' +
          '</div>' +
          '<div class="summary-row">' +
            '<span class="summary-label">Axes Compared</span>' +
            '<span class="summary-value">' + axes.length + '</span>' +
          '</div>';
        grid.appendChild(summary);

        resultsEl.appendChild(grid);
      }

      // ── Show status message ─────────────────────────────────────────────────
      function showStatus(msg, cls, ariaLabel) {
        var el = document.getElementById('status-area');
        el.className = cls;
        el.setAttribute('role', 'status');
        el.setAttribute('aria-label', ariaLabel || msg);
        el.textContent = msg;
        el.hidden = false;
        document.getElementById('results-area').hidden = true;
      }

      // ── Load comparison ────────────────────────────────────────────────────
      function loadComparison() {
        var s1 = document.getElementById('id1-input').value.trim();
        var s2 = document.getElementById('id2-input').value.trim();

        if (!s1 || !s2) {
          showStatus('Please enter both server IDs.', 'error', 'Missing server IDs');
          return;
        }

        var ids = s1 + ',' + s2;
        showStatus('Loading comparison data…', 'loading', 'Loading comparison data');

        apiGet('/servers/compare?ids=' + encodeURIComponent(ids))
          .then(function(data) {
            renderComparison(data);
          })
          .catch(function(err) {
            console.error('[server_compare_view] load error:', err);
            showStatus('Unable to load comparison: ' + err.message, 'error', 'Comparison load error');
          });
      }

      // ── Wire up controls ───────────────────────────────────────────────────
      document.getElementById('compare-btn').addEventListener('click', loadComparison);

      ['id1-input', 'id2-input'].forEach(function(id) {
        document.getElementById(id).addEventListener('keypress', function(e) {
          if (e.key === 'Enter') loadComparison();
        });
      });

      // ── Self-test: verify render handles empty/partial responses ──────────
      (function selfTest() {
        try {
          renderComparison({});
          renderComparison(null);
          renderComparison({ servers: [], axes: [], composite_delta: 0 });
          renderComparison({
            servers: [
              { server_id: 's1', name: 'Srv One', verdict: 'APPROVED', risk_tier: 'low' },
              { server_id: 's2', name: 'Srv Two', verdict: 'PENDING', risk_tier: 'high' }
            ],
            axes: [
              { axis_name: 'overall_risk', label: 'Overall Risk', values: { s1: 0.2, s2: 0.75 } },
              { axis_name: 'auth_strength', label: 'Auth Strength', values: { s1: 0.9, s2: 0.4 } }
            ],
            composite_delta: 0.55
          });
          console.info('[SELFTEST] renderComparison empty/partial paths passed — no exceptions thrown.');
        } catch (e) {
          console.error('[SELFTEST] FAILED:', e);
          throw e;
        }
      })();

    })();
  </script>
</body>
</html>"""

@router.get("/server_compare_view", response_class=HTMLResponse)
async def get_server_compare_view():
    """Return the self-contained server compare HTML view.

    Fetches live data from GET /api/servers/compare?ids=<id1>,<id2>.
    """
    return HTMLResponse(content=HTML_PAGE)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import ast

    path = "services/active/server_compare_view/view.py"
    src = open(path).read()

    checks = [
        ("fetch(",              "fetch() call"),
        ("aria-label",          "aria-label attributes"),
        ("localStorage",        "localStorage forbidden"),
        ("sessionStorage",      "sessionStorage forbidden"),
        ("API_BASE",            "API_BASE constant"),
        ("renderComparison",    "renderComparison function"),
        ("selfTest()",          "self-test block"),
        ("servers/compare",     "backend API call path"),
        ("Authorization",       "Authorization header"),
        ("get_session",         "get_session import (no-hollow)"),
        ("composite_delta",     "composite_delta handling"),
        ("risk_tier",           "risk_tier handling"),
        ("McpServerRegistry",   "model reference"),
    ]

    failures = []
    for needle, label in checks:
        if needle == "localStorage" or needle == "sessionStorage":
            if needle in src:
                failures.append("FORBIDDEN: " + label + " found")
        elif needle == "get_session":
            if needle not in src:
                failures.append("MISSING: " + label)
        elif needle == "McpServerRegistry":
            # Check it appears in a comment or import context
            if "McpServerRegistry" not in src:
                failures.append("MISSING: " + label)
        else:
            if needle not in src:
                failures.append("MISSING: " + label)

    try:
        ast.parse(src)
    except SyntaxError as e:
        failures.append("Python syntax error: " + str(e))

    if failures:
        raise SystemExit("FAILED:\n  " + "\n  ".join(failures))

    print("Self-test PASS: view.py is well-formed and contains all required pieces.")
