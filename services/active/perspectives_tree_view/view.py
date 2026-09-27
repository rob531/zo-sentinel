<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Perspective Tree View</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --border: #e2e8f0;
      --text-primary: #1e293b;
      --text-secondary: #64748b;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --tier-escalation: #dc2626;
      --tier-escalation-bg: #fef2f2;
      --tier-deescalation: #16a34a;
      --tier-deescalation-bg: #f0fdf4;
      --tier-stable: #64748b;
      --tier-stable-bg: #f8fafc;
      --tier-new: #7c3aed;
      --tier-new-bg: #f5f3ff;
      --radius: 8px;
      --shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text-primary);
      line-height: 1.5;
      min-height: 100vh;
    }
    .container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
    header {
      display: flex; justify-content: space-between; align-items: flex-start;
      margin-bottom: 24px; flex-wrap: wrap; gap: 16px;
    }
    .title-block h1 {
      font-size: 1.5rem; font-weight: 600; color: var(--text-primary);
    }
    .title-block .subtitle {
      font-size: 0.875rem; color: var(--text-secondary); margin-top: 4px;
    }
    .controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .param-group { display: flex; flex-direction: column; gap: 4px; }
    .param-group label {
      font-size: 0.75rem; font-weight: 500; color: var(--text-secondary);
      text-transform: uppercase; letter-spacing: 0.5px;
    }
    .param-group input {
      padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px;
      font-size: 0.875rem; min-width: 200px; background: var(--card-bg);
    }
    .param-group input:focus {
      outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(59,130,246,0.15);
    }
    .btn {
      padding: 8px 16px; border-radius: 6px; font-size: 0.875rem; font-weight: 500;
      cursor: pointer; border: none; transition: background 0.15s;
    }
    .btn-primary {
      background: var(--accent); color: #fff;
    }
    .btn-primary:hover { background: var(--accent-hover); }
    .btn-primary:focus {
      outline: 2px solid var(--accent); outline-offset: 2px;
    }
    .summary-bar {
      display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap;
    }
    .summary-stat {
      background: var(--card-bg); padding: 12px 16px; border-radius: var(--radius);
      border: 1px solid var(--border); box-shadow: var(--shadow);
    }
    .summary-stat .label {
      font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .summary-stat .value {
      font-size: 1.25rem; font-weight: 600; margin-top: 2px;
    }
    .tier-group {
      background: var(--card-bg); border-radius: var(--radius); margin-bottom: 16px;
      border: 1px solid var(--border); box-shadow: var(--shadow); overflow: hidden;
    }
    .tier-group-header {
      padding: 12px 16px; display: flex; justify-content: space-between;
      align-items: center; font-weight: 600; font-size: 0.875rem;
    }
    .tier-group-header.escalation {
      background: var(--tier-escalation-bg); color: var(--tier-escalation);
    }
    .tier-group-header.deescalation {
      background: var(--tier-deescalation-bg); color: var(--tier-deescalation);
    }
    .tier-group-header.stable {
      background: var(--tier-stable-bg); color: var(--tier-stable);
    }
    .tier-group-header.new {
      background: var(--tier-new-bg); color: var(--tier-new);
    }
    .tier-group-header .count {
      font-size: 0.75rem; font-weight: 500; opacity: 0.8;
    }
    .server-list { padding: 0; }
    .server-item {
      padding: 12px 16px; border-bottom: 1px solid var(--border);
      display: flex; justify-content: space-between; align-items: center;
    }
    .server-item:last-child { border-bottom: none; }
    .server-info { display: flex; flex-direction: column; gap: 2px; }
    .server-name { font-weight: 500; font-size: 0.875rem; }
    .server-meta { font-size: 0.75rem; color: var(--text-secondary); }
    .server-tier {
      font-size: 0.75rem; font-weight: 500; padding: 4px 8px;
      border-radius: 4px; text-transform: uppercase; letter-spacing: 0.3px;
    }
    .server-tier.high { background: var(--tier-escalation-bg); color: var(--tier-escalation); }
    .server-tier.medium { background: #fef3c7; color: #b45309; }
    .server-tier.low { background: var(--tier-deescalation-bg); color: var(--tier-deescalation); }
    .server-tier.unknown { background: var(--tier-stable-bg); color: var(--tier-stable); }
    .event-badge {
      font-size: 0.625rem; padding: 2px 6px; border-radius: 3px; margin-left: 8px;
      font-weight: 500; text-transform: uppercase;
    }
    .event-badge.seen { background: #dbeafe; color: #1e40af; }
    .event-badge.unseen { background: #fef3c7; color: #b45309; }
    .empty-state, .loading-state, .error-state {
      text-align: center; padding: 48px 24px; border-radius: var(--radius);
    }
    .loading-state { color: var(--text-secondary); }
    .loading-state::before {
      content: ''; display: inline-block; width: 32px; height: 32px;
      border: 3px solid var(--border); border-top-color: var(--accent);
      border-radius: 50%; animation: spin 0.8s linear infinite;
      vertical-align: middle; margin-right: 12px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .error-state {
      background: var(--tier-escalation-bg); color: var(--tier-escalation);
      border: 1px solid #fecaca;
    }
    .empty-state {
      background: var(--card-bg); border: 2px dashed var(--border);
      color: var(--text-secondary);
    }
    .spinner { display: inline-block; }
    @media (max-width: 640px) {
      .param-group input { min-width: 140px; }
      .summary-bar { gap: 8px; }
      .summary-stat { padding: 8px 12px; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-block">
        <h1>Perspective Tree</h1>
        <p class="subtitle">Server tier transitions grouped by change type</p>
      </div>
      <div class="controls">
        <div class="param-group">
          <label for="perspectiveId">Perspective ID</label>
          <input type="text" id="perspectiveId" placeholder="e.g. persp-456"
                 aria-label="Perspective ID input">
        </div>
        <div class="param-group">
          <label for="orgId">Organization ID</label>
          <input type="text" id="orgId" placeholder="e.g. org-456"
                 aria-label="Organization ID input">
        </div>
        <button class="btn btn-primary" id="loadBtn" aria-label="Load perspective tree data">
          Load
        </button>
      </div>
    </header>

    <div id="summaryBar" class="summary-bar" style="display:none;" role="region" aria-label="Summary statistics">
      <div class="summary-stat">
        <div class="label">Perspective</div>
        <div class="value" id="perspectiveName">—</div>
      </div>
      <div class="summary-stat">
        <div class="label">Total Events</div>
        <div class="value" id="eventsCount">—</div>
      </div>
      <div class="summary-stat">
        <div class="label">Last Updated</div>
        <div class="value" id="lastUpdated">—</div>
      </div>
    </div>

    <main id="mainContent" role="main">
      <div class="empty-state" role="status" aria-live="polite">
        Enter a Perspective ID and Organization ID, then click Load to view the tree.
      </div>
    </main>
  </div>

  <script>
    // In-memory auth state — no localStorage/sessionStorage
    const authState = { token: null };
    (function initAuth() {
      authState.token = (window !== window.top && window.top.__ZO_AUTH__)
        ? window.top.__ZO_AUTH__.bearer
        : 'Bearer placeholder';
    })();

    const API_BASE = '/api';

    // Self-test: verify render functions handle empty/partial responses without throwing
    (function selfTest() {
      const renders = [
        renderEmpty, renderError, renderLoading, renderTree
      ];
      const testCases = [
        {}, { tree: [] }, { tree: null }, { tree: undefined },
        { tree: [], events_count: 0 }, { perspective_name: '', tree: [] },
        { perspective_name: 'Test', tree: [{ group: 'x', label: 'X', servers: [] }] },
        { perspective_name: 'Test', tree: [{ group: 'x', servers: [{ server_id: 's1', name: 'Server', risk_tier: 'high', events: [] }] }] },
      ];
      let passed = 0;
      for (const render of renders) {
        for (const data of testCases) {
          try {
            render(data);
            passed++;
          } catch (e) {
            console.error('[SELFTEST] FAILED render with data:', JSON.stringify(data), e);
            throw e;
          }
        }
      }
      console.info(`[SELFTEST] All ${passed}/${renders.length * testCases.length} empty/partial render paths passed.`);
    })();

    function renderLoading() {
      return '<div class="loading-state" role="status" aria-live="polite" aria-label="Loading data">Loading data<span class="spinner"></span></div>';
    }

    function renderError(msg) {
      return `<div class="error-state" role="alert" aria-live="assertive">${escapeHtml(msg)}</div>`;
    }

    function renderEmpty() {
      return '<div class="empty-state" role="status" aria-live="polite">No servers found in this perspective tree.</div>';
    }

    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }

    function formatDate(val) {
      if (!val) return '—';
      try {
        return new Date(val).toLocaleString();
      } catch {
        return String(val);
      }
    }

    function getTierClass(tier) {
      const t = String(tier).toLowerCase();
      if (t === 'high' || t === 'critical') return 'high';
      if (t === 'medium') return 'medium';
      if (t === 'low') return 'low';
      return 'unknown';
    }

    function getGroupClass(group) {
      if (group === 'tier_escalation') return 'escalation';
      if (group === 'tier_deescalation') return 'deescalation';
      if (group === 'no_previous_tier') return 'new';
      return 'stable';
    }

    function renderServer(server) {
      const seenCount = (server.events || []).filter(e => e.seen).length;
      const totalEvents = (server.events || []).length;
      const eventsLabel = totalEvents > 0
        ? `<span class="event-badge ${seenCount === totalEvents ? 'seen' : 'unseen'}" aria-label="${seenCount} of ${totalEvents} events seen">${totalEvents} event${totalEvents !== 1 ? 's' : ''}</span>`
        : '';

      return `
        <div class="server-item" role="listitem">
          <div class="server-info">
            <span class="server-name">${escapeHtml(server.name || 'Unknown')}</span>
            <span class="server-meta">
              ID: ${escapeHtml(server.server_id || '')}
              ${server.last_seen ? ' · Last seen: ' + formatDate(server.last_seen) : ''}
            </span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            ${eventsLabel}
            <span class="server-tier ${getTierClass(server.risk_tier)}" aria-label="Risk tier: ${server.risk_tier}">
              ${escapeHtml(server.risk_tier || 'unknown')}
            </span>
          </div>
        </div>`;
    }

    function renderGroup(group) {
      const servers = group.servers || [];
      const serversHtml = servers.length > 0
        ? `<div class="server-list" role="list">${servers.map(renderServer).join('')}</div>`
        : `<div class="server-list" style="padding:16px;color:var(--text-secondary);font-size:0.875rem;">No servers in this group.</div>`;

      return `
        <section class="tier-group" aria-labelledby="group-${group.group}">
          <div class="tier-group-header ${getGroupClass(group.group)}" id="group-${group.group}">
            <span>${escapeHtml(group.label || group.group)}</span>
            <span class="count">${servers.length} server${servers.length !== 1 ? 's' : ''}</span>
          </div>
          ${serversHtml}
        </section>`;
    }

    function renderTree(data) {
      const tree = data.tree || [];
      if (tree.length === 0) return renderEmpty();

      // Update summary bar
      const summaryBar = document.getElementById('summaryBar');
      const perspectiveName = document.getElementById('perspectiveName');
      const eventsCount = document.getElementById('eventsCount');
      const lastUpdated = document.getElementById('lastUpdated');

      if (perspectiveName) perspectiveName.textContent = data.perspective_name || '—';
      if (eventsCount) eventsCount.textContent = data.events_count ?? '—';
      if (lastUpdated) lastUpdated.textContent = formatDate(data.last_updated);
      if (summaryBar) summaryBar.style.display = 'flex';

      return `<div role="list" aria-label="Tier groups">${tree.map(renderGroup).join('')}</div>`;
    }

    async function loadTree() {
      const perspectiveId = document.getElementById('perspectiveId').value.trim();
      const orgId = document.getElementById('orgId').value.trim();

      if (!perspectiveId || !orgId) {
        document.getElementById('mainContent').innerHTML = renderError('Both Perspective ID and Organization ID are required.');
        return;
      }

      const mainContent = document.getElementById('mainContent');
      mainContent.innerHTML = renderLoading();

      // Update URL params for shareability
      const url = new URL(window.location);
      url.searchParams.set('perspective_id', perspectiveId);
      url.searchParams.set('org_id', orgId);
      window.history.replaceState({}, '', url);

      try {
        const resp = await fetch(
          `${API_BASE}/perspectives/tree?perspective_id=${encodeURIComponent(perspectiveId)}&org_id=${encodeURIComponent(orgId)}`,
          {
            method: 'GET',
            headers: {
              'Authorization': authState.token,
              'Accept': 'application/json'
            }
          }
        );

        if (!resp.ok) {
          const errText = await resp.text().catch(() => `HTTP ${resp.status}`);
          throw new Error(`Request failed: ${errText}`);
        }

        const data = await resp.json();
        mainContent.innerHTML = renderTree(data);
      } catch (e) {
        console.error('[perspectives_tree_view] fetch error:', e);
        mainContent.innerHTML = renderError(`Unable to load tree: ${e.message}`);
      }
    }

    // Wire up controls
    document.getElementById('loadBtn').addEventListener('click', loadTree);

    // Allow Enter key in inputs
    document.getElementById('perspectiveId').addEventListener('keydown', e => {
      if (e.key === 'Enter') loadTree();
    });
    document.getElementById('orgId').addEventListener('keydown', e => {
      if (e.key === 'Enter') loadTree();
    });

    // Pre-fill from URL params on load
    (function prefillFromUrl() {
      const params = new URLSearchParams(window.location.search);
      const pid = params.get('perspective_id');
      const oid = params.get('org_id');
      if (pid) document.getElementById('perspectiveId').value = pid;
      if (oid) document.getElementById('orgId').value = oid;
      if (pid && oid) loadTree();
    })();
  </script>
</body>
</html>
