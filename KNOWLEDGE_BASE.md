# ZO-SENTINEL KNOWLEDGE BASE
## Critical rules injected into every build prompt.

## WRITE SERVICE CONTRACT
- POST http://127.0.0.1:8772/write  body: {table, rows:{...}, wait:True}  -- 'rows' NOT 'row'
- POST http://127.0.0.1:8772/query  body: {sql}  -> {rows:[...], count:N}  -- SELECT only
- POST http://127.0.0.1:8772/execute body: {sql}  -> {ok:true}  -- DDL/DML only, no rows returned
- NEVER import duckdb directly. NEVER call duckdb.connect(). ALL DB via write_service HTTP.
- INSERT OR IGNORE not DuckDB-compatible -- use ON CONFLICT DO NOTHING or ON CONFLICT DO UPDATE

## PORT REGISTRY
- 8772: write_service (DuckDB gateway)
- 8773: inference_router
- 8775: email_guid_auth
- 8776: manual_override_api
- 8777: advanced_filter_api
- 8779: forensic_detail_api
- 8780: approval_workflow
- 8781: registry_api
- 8782: search_api
- 8784: nl_query_engine
- 8785: rule_engine_api
- 8790: ui_server (Sentinel dashboard)

## DAEMON TEMPLATE
Every daemon must have:
  1. run() function with while True loop and time.sleep(POLL_SECS)
  2. send_heartbeat() called every cycle: POST /write table=service_health rows={service, last_heartbeat}
  3. check_single_instance() using PID file at /tmp/{service_name}.pid
  4. if __name__ == '__main__': run()
  5. GET /health route returning {status:'ok', service:name, uptime:seconds}

## FASTAPI SERVICE TEMPLATE
  from fastapi import FastAPI
  import uvicorn
  app = FastAPI()
  def run(): uvicorn.run(app, host='127.0.0.1', port=PORT)
  if __name__ == '__main__': run()

## WEB SCRAPER SKILL (use for external HTTP enrichment)
Available at /home/workspace/Skills/web-scraper/
Python import pattern (for daemons that need to scrape external URLs):

  import sys
  sys.path.insert(0, '/home/workspace')
  # Sync wrapper (use in non-async daemons)
  import asyncio
  from web_scraper_integration import quick_scrape, structured_extract

  # Simple page fetch (returns markdown text)
  result = asyncio.run(quick_scrape('https://npmjs.com/package/mcp-server-xyz'))
  if result and result.get('success'):
      content = result['content'][:5000]

  # Structured extraction with CSS schema
  schema = {
      'name': 'grades',
      'baseSelector': '.grade-item',
      'fields': [
          {'name': 'label', 'selector': '.label', 'type': 'text'},
          {'name': 'score', 'selector': '.score', 'type': 'text'}
      ]
  }
  data = asyncio.run(structured_extract('https://example.com', schema))

USE THIS instead of raw requests.get() + BeautifulSoup for external scraping.
It handles JS rendering, rate limiting, and retries automatically.
CLI: python3 /home/workspace/Skills/web-scraper/scripts/scraper.py scrape <url>

## WEBAPP TESTING SKILL (use for UI phase-gate tests)
Available at /home/workspace/Skills/webapp-testing/
Playwright-based headless browser testing.

  from playwright.sync_api import sync_playwright
  with sync_playwright() as p:
      browser = p.chromium.launch(headless=True)
      page = browser.new_page()
      page.goto('http://127.0.0.1:8790')
      page.wait_for_load_state('networkidle')  # CRITICAL
      # Inspect, screenshot, verify
      page.screenshot(path='/tmp/ui_check.png')
      browser.close()

Sentinel UI is at http://127.0.0.1:8790
Use in L6 UI smoke tests and phase checkpoint verification.

## HTML FILE RULES (critical -- avoids syntax failures)
- Output ONLY raw HTML. First line must be <!DOCTYPE html>
- ALL <script> blocks must be syntactically valid JavaScript
- Template literals (backticks) must close on same nesting level
- fetch() calls must have .catch() error handling
- Never split string literals across lines without continuation
- Use zo-html-coder model for HTML generation (not zo-backend-coder)

## SHELL SCRIPT RULES
- First line must be #!/bin/bash
- DO NOT run Python syntax checker on .sh files
- Shell scripts are validated for shebang only, not Python AST

## KNOWN TABLE SCHEMAS (key columns only)
- mcp_server_registry: server_id(PK), name, url, description, trust_score, verdict, registry_source, scan_count
- mcp_signal_scores: server_id, signal_name, score, evidence, scored_at  (NO auto-id -- no ON CONFLICT)
- mcp_threat_associations: server_id, threat_type, severity, evidence, reported_at  (NO auto-id)
- mcp_risk_register: server_id(PK), risk_tier, risk_rank, threat_count, computed_at
- audit_log: id(PK), target_server_id, event_type, actor, detail, created_at  (column is target_server_id NOT server_id)
- auth_tokens: token_id(PK), action, mcp_name, submission_id, admin_email, expires_at, used, used_at
- service_health: service(PK), last_heartbeat

## CONTROL PLACEMENT RULE (standing -- 2026-08-26)
When you build a control, prove it sits on the path that carries the volume.
Measure the build counts of each lane and state which lane the control covers.
A control is not "in place" until that number is written down.

- The June 2026 schema gate (#1006) was real and correct. It was wired to the
  goose-canary lane at ~8 builds/day while the ENGINE path wrote 588 files in a
  single day (2026-08-11). Right control, wrong path -- so it caught nothing,
  and the phantom-table backlog it was built to prevent accumulated underneath
  it for six weeks.
- The same shape has appeared three more times: the promoter missing from
  watchdog.sh, four dark lanes, and (found 2026-08-26) the schema-prm CI gate,
  which runs on 100% of PRs but inspects ROOT-LEVEL .py only while the engine
  emits into services/staged/** -- about 2% coverage of what it exists to check.
- "It runs on every PR" is NOT coverage. Check what the job's own file filter
  admits, then compare that to where the emissions actually land.
- A lane with zero runs is a control with zero coverage. goose-canary last ran
  2026-08-10; anything gated only there is currently gating nothing.

Two numbers, every time: builds per lane, and the fraction the control sees.

## PRODUCT ROADMAP (PRD v1 — 2026-05-20)
Zo Sentinel is evolving from a daemon+API into a production SaaS product.
Directive generator should prioritise features from this roadmap in order.

### PHASE 1 — React/Vite UI Foundation (CURRENT PRIORITY)
Scaffold a proper React + Vite build pipeline in zo_sentinel/ui_react/
Replace the current single-file FastAPI inline HTML with a built React app.
Design system: Tailwind CSS + shadcn/ui components.
Font: DM Sans or Space Grotesk. No pure black/white — use slate-900/slate-50.
Layout: collapsible left sidebar (primary nav) + top bar (profile/search/settings).
Build output: zo_sentinel/ui_react/dist/ served by ui_server.py as static files.

Key components to build:
- AppShell: sidebar + topbar layout wrapper
- TrustScoreGauge: SVG ring showing 0-100 trust score with verdict colour
- ServerDataGrid: sortable/paginated/filterable table with bulk actions
- VerdictBadge: colour-coded pill (TRUSTED=green, AMBER=yellow, UNTRUSTED=red, UNKNOWN=grey, KNOWN_THREAT=darkred)
- SignalBreakdown: expandable panel showing all 6 signal scores per server
- ServiceHealthPanel: daemon status grid with heartbeat age
- EmptyState: consistent empty state with icon + CTA for zero-data views

### PHASE 2 — Auth + API Key Management
Add auth_tokens table (already exists) to UI.
Login flow: email + token (no password — token-based for agent/API consumers).
API key management page: generate, revoke, list tokens with scopes.
RBAC roles: admin (full), analyst (read + submit), viewer (read only).
All /api/* routes must check Authorization: Bearer <token> header.
Store tokens in auth_tokens table (already exists in schema).

### PHASE 3 — Onboarding + Empty States
First-run wizard: connect to WriteService -> verify DB -> run first scan.
Empty state for zero servers: show "Submit your first MCP" CTA.
Onboarding tour: highlight trust score, signal breakdown, watchlist.
Zero-data states for every table — never show a blank white panel.

### PHASE 4 — RBAC + Multi-tenant (future)
Workspace concept: each tenant has isolated registry view.
Not required until first external customer.

### PHASE 5 — Billing (future)
Subscription tiers: Free (100 MCPs), Pro (10K MCPs), Enterprise (unlimited).
Not required until monetising.

## UI TECHNICAL CONSTRAINTS
- WriteService read endpoint: http://127.0.0.1:8772/query (POST, {sql: string})
- WriteService returns: {rows: [...]} for SELECT, [{Count: N}] for INSERT
- No /write endpoint — all writes via SQL INSERT through /query
- Sentinel UI port: 8790
- React build must output to ui_react/dist/ and be served as static files by ui_server.py
- No external CDN dependencies in production build
- All API routes must handle WriteService 400/500 gracefully — never show raw errors

## GOOSE INTEGRATION
Goose (Block open-source agent) is being integrated as Tier 1 autonomous builder.
Install: pip install goose-ai or pipx install goose-ai
CLI: goose run --instructions "<spec>" --with-extension <mcp-url>

### Goose Ladder Position
Tier 0: Ollama phi3:mini (classifier, filter only)
Tier 1: Goose + Ollama (headless, free, ZoComputer + Tower)
Tier 2: Goose + MiniMax (complex generation, flat-rate)
Tier 3: Claude Code (supervised UI/UX, human in loop)
Tier 4: Dispatch (training, strategic oversight)

### Goose Directive Format
JSON directives already used by zo_sentinel_builder are compatible with Goose.
Goose executor reads directive from mesh_memory or directives/pending/ and runs:
  goose run --instructions "$(cat directive.spec)"             --with-builtin developer             --model minimax

### Goose ZoComputer Integration
Install path: /home/workspace/venvs/goose/
Config: /home/workspace/zo_sentinel/goose_config.yaml
MCP connection: --with-extension http://127.0.0.1:3891 (zo_mcp_server external proxy)
Output: writes to /home/workspace/shared/outputs/goose/ for deploy_consumer pickup
Log: /home/workspace/logs/goose_runner.log

### goose_runner.py (to be built)
Daemon that:
1. Polls mesh_memory for directives tagged source=goose_tier1 or complexity=low
2. Writes directive spec to /tmp/goose_directive.txt
3. Runs: goose run --instructions "$(cat /tmp/goose_directive.txt)" --model ollama/phi3:mini
4. On completion: marks directive done, writes output to shared/outputs/goose/
5. Falls back to MiniMax if Ollama fails

### Goose Tower Integration  
Tower (warm compute): run Goose with Ollama for low-complexity tasks.
Install Goose on Tower: pip install goose-ai in Tower venv.
Trigger via ZoWarmWorker probe spec:
  {"task": "goose_run", "directive_file": "shared/directives/<name>.json", "model": "ollama"}
Invoke-Probe.ps1 handles goose_run task type: reads directive, runs goose CLI, writes output.

### Directive Routing (complexity-based)
In normalize_directive(), route by complexity:
  complexity=low  -> source=goose_tier1  (Goose + Ollama, free)
  complexity=medium -> source=goose_tier2 (Goose + MiniMax)
  complexity=high -> zo_sentinel_builder (existing MINIMAX builder)
  complexity=ui -> Claude Code SESSION_LOG directive

## SIGNAL QUALITY TARGETS (2026 Q2)
Current state (2026-05-20):
  mcp_server_registry: 9,662 servers
  mcp_signal_scores: 6,490 scored
  mcp_fingerprints: 6,334
  mcp_attestations: 6,260
  mcp_ecosystems_metadata: 6,095
  mcp_threat_associations: 8 (critically low)
  mcp_registry_facts: 45 (critically low)

Targets:
  Week 1: 10,000 registry entries
  Week 2: 15,000 registry entries, signal_scores coverage >50%
  Week 3: 20,000 registry entries, all 6 signals >30% coverage
  Month end: 20,000 fully scored (all 6 signals), Qwen 2.5 classifier deployed

## SLM TRAINING INTEGRATION
Qwen 2.5 0.5B being fine-tuned via gold teacher dual SFT to classify MCPs into risk rungs.
Training corpus filter: WHERE verdict != unknown AND EXISTS (SELECT 1 FROM mcp_signal_scores WHERE server_id=r.server_id)
Only use fully-scored servers as training examples. Never use verdict=unknown rows.
Risk rungs (classification targets):
  KNOWN_THREAT, HIGH_RISK_ISOLATED, CAUTION_LIMITED, AMBER_UNVERIFIED,
  TRUSTED_RESEARCH, ENTERPRISE_CONTROLLED
Model output replaces signal_analyser tier for batch scoring of new registry entries.
Deploy path: Tower CPU inference -> ZoComputer via shared/outputs/ bridge.
