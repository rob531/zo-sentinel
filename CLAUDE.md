# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Autonomy

You have **full standing operational clearance** — the user approves everything ("YES to all"),
so do not stall the workflow with confirmation check-ins for standard work.

Pre-approved, execute without asking:

- Run any Python script or shell command
- Install packages via `pip install -q <pkg>`
- Read, write, create, modify any file in this directory
- Start and stop local servers
- Make HTTP requests to localhost
- Write directives to `directives/pending/`
- Overwrite existing files when fixing issues
- Re-run tests after applying a fix
- Edit `ui/` HTML, CSS, JS files directly
- Add new API routes to `ui_server.py`
- The **entire PR flow**: branch → commit → push → open PR → **squash-merge your own PR** →
  `git push --force-with-lease` on a feature branch
- **Deploy to the host clone**: `git fetch origin && git reset --hard origin/main`, then relaunch
  the affected daemon (`tools/run_publisher_daemon.sh` / `tools/reload_daemon.sh`)

**Never ask for permission. Never pause for confirmation. Execute, observe, improve, re-test, repeat.**

**The only things to surface first** (and only these): violating a hard constraint (a direct write to
`/home/workspace/` instead of going through git, a raw-env/`.env` secret instead of AgentVault,
touching supervisord, a direct DuckDB import); irreversible destruction **outside** the normal flow
(force-pushing `main`, dropping a production table, deleting data you didn't create, mass file
deletion); or printing/transmitting a secret value (never do this). Prefer `--force-with-lease` over
bare `--force`, and verify-then-proceed over ask-then-wait.

> For build-pipeline / mesh work and the full hard-constraint list, see `ZO_SENTINEL_PRIMER.md`.

## LOCO CHAIRMAN — mandatory governance read

`LOCO_CHAIRMAN.md` is the locum of truth: the standing authority on **what
counts as finished**. Its consultation contract is not optional:

- **Session start:** read `chairman/CHECKPOINT.md` (resume any in-flight
  capture first), then `chairman/QUEUE.md` for the next unit of work.
- **On any new finding:** run the interrogatives (`LOCO_CHAIRMAN.md` §2) and
  label it with a gap class (§4).
- **Before any "done"/"fixed"/"closed" claim:** state its closure grade (§3).
  Only C4 may be called closed.
- **Low context / low tokens / session ending:** write, commit, and PUSH a
  capture to `chairman/CHECKPOINT.md` per §10 before stopping.
- **Session end:** update touched Gap Register rows (§6) — no silent drops.

> For the cross-host builder self-repair daemons, see "Builder self-repair harness" below.

## Primary mission

Build a rich, responsive, genuinely useful MCP trust intelligence UI. A clean treewalk is the minimum bar — not the goal. The goal is a UI a security analyst would actually want to use.

What good looks like:

- **At-a-glance trust posture** — verdict + risk breakdown visible without scrolling
- **Actionable data** — every row tells you what to do next
- **Live feel** — data refreshes, health status pulses, no stale state
- **Fast filtering** — verdict, risk tier, search, sort all work instantly
- **Drill-down** — click a server → signals, attestations, scan history
- **Alerts surface** — CRITICAL/UNTRUSTED servers are impossible to miss

## Improvement loop

```
START
  1. Ensure server running              -> run_local.ps1 (or restart)
  2. Treewalk — fix all CRITICAL/HIGH   -> python treewalk.py
  3. Audit current UI                   -> GET / and read HTML, or screenshot
  4. Pick highest-value improvement     -> see backlog below
  5. Implement it                       -> edit ui_server.py + inline HTML/CSS/JS as needed
  6. Restart server, verify in browser  -> GET http://localhost:8790
  7. Append to SESSION_LOG.md
  8. goto 4
END CONDITION: backlog exhausted AND treewalk clean
```

## Improvement backlog (priority order)

### P0 — Stability (must precede P1)
- [ ] All treewalk routes return 200
- [ ] No 500s from WriteService timeout — graceful fallback (the catch-all middleware already does this; verify it holds)
- [ ] `verdict` + `risk` filters work on `/api/servers`
- [ ] Pagination works (`limit` + `offset`)

### P1 — At-a-glance trust posture
- [ ] Dashboard header: total servers + verdict breakdown as coloured badges (TRUSTED=green, AMBER=yellow, UNTRUSTED=red, UNKNOWN=grey)
- [ ] Summary bar visible on every page without scrolling
- [ ] CRITICAL risk servers highlighted in red, float to top by default
- [ ] Live server count (auto-refresh every 30s)

### P2 — Filtering + search
- [ ] Filter bar: verdict buttons, risk-tier buttons, free-text search
- [ ] All filters combinable, client-side applied for speed
- [ ] Active filter shown as removable chip
- [ ] URL params reflect active filters (shareable links)
- [ ] Sort by trust_score, name, risk_tier, last_seen

### P3 — Server detail drill-down
- [ ] Click any server row → expand inline or open detail panel
- [ ] Detail shows: trust_score gauge, all signals, attestations, scan history
- [ ] Copy server_id / URL buttons
- [ ] "Why untrusted" explanation from signal breakdown

### P4 — Live health dashboard
- [ ] Service-health panel: each daemon with status indicator
- [ ] Green/amber/red dot + last heartbeat time
- [ ] Stale heartbeat (>1hr) → warning automatically
- [ ] Pipeline throughput: servers assessed today / this week

### P5 — Sentinel-specific intelligence
- [ ] Top 10 most trusted (leaderboard)
- [ ] Top 10 highest risk (watchlist)
- [ ] Recently assessed (last 24h)
- [ ] Verdict drift: servers that changed verdict recently
- [ ] Trust score distribution histogram

### P6 — UX polish
- [ ] Mobile responsive
- [ ] Dark mode is intentional
- [ ] Loading skeletons, not blank white flash
- [ ] Empty states with helpful message (not blank table)
- [ ] Error banner: "WriteService unreachable" rather than silent empty table
- [ ] Keyboard navigation on table

## UI tech stack

Use what's in the inline HTML/CSS/JS of `ui_server.py` (or anything in `ui/` once added). **No build steps. No npm.** Prefer:

- Vanilla JS or Alpine.js (CDN) for interactivity
- Tailwind CSS (CDN) for styling
- Chart.js (CDN) for charts
- `setInterval` or SSE for live refresh

Avoid: React, Vue, webpack, node_modules — no build pipeline.

## API routes (extend as needed)

| Route | Purpose |
|---|---|
| `GET /` | Dashboard root (rich landing) |
| `GET /healthz` and `GET /health` | Liveness (`/health` is alias) |
| `GET /api/servers` | Server list — supports `?verdict=&risk=&limit=&offset=&q=` |
| `GET /api/servers/{id}` | Single server detail + signals |
| `GET /api/recent` | Recently assessed |
| `GET /api/search` | Rich payload (results + signals + threats + risk + attestation + history) |
| `GET /api/registry` | Registry catalog |
| `GET /api/attestations` | Attestation list |
| `GET /api/mesh-events` | Recent mesh events |
| `GET /api/service-health` | Daemon health |
| `GET /api/stats` | Aggregate counts by verdict/risk |
| `GET /api/servers/recent` | Assessed in last 24h |
| `GET /api/servers/watchlist` | CRITICAL + UNTRUSTED |
| `GET /api/audit` | Audit log |
| `POST /api/submit` | Pydantic-validated submission |
| `GET /api/admin/*` and `/admin-threats`, `/admin-risk` | Admin views |
| `GET /submit`, `GET /mcp/{id}` | HTML pages |

Add missing routes directly to `ui_server.py` — no permission needed.

## What this repo is

`zo-sentinel` is the **rich UI server** (`ui_server.py`, port 8790) and the **InfoSec approval workflow** (`approval_workflow.py`, port 8780) extracted from the larger ZO-SENTINEL MCP trust-intelligence system. Both are **stateless clients** of `write_service` on `127.0.0.1:8772`. They never touch DuckDB directly; all reads/writes go through `ws_query` / `ws_write` / `ws_execute` helpers.

## Run locally

```powershell
.\run_local.ps1
```

Creates `.venv` (Python 3.11), installs `requirements.txt` + `dev/requirements-dev.txt` (wheels only), starts `dev/mock_write_service.py` on 8772, waits for `/healthz`, runs `ui_server.py` on 8790. Ctrl-C tears the mock down.

The mock pattern-matches SQL on real table names (`mcp_server_registry`, `mcp_signal_scores`, `mcp_threat_associations`, `mcp_risk_register`, `mcp_attestations`, `mcp_assessment_history`, `audit_log`). Unknown SQL returns empty results, not 500 — so the UI degrades gracefully. Fixtures (`dev/fixtures.py`) cover all seven verdict states.

**Don't chase external URLs.** `https://zo-writeservice-robinc.zocomputer.io` is provisioned in Cloudflare but the upstream 404s every path and Cloudflare bans `Python-urllib/*`. Per `docs/ARCHITECTURE.md:204-205`, write_service is intentionally not externally reachable. Live production data is reachable via the `zo_db_query` MCP tool at `https://zo-mcp-server-robinc.zocomputer.io/mcp` (no auth needed) — but for UI iteration, use the local mock.

## DB access from scripts

```python
from zobridge import query, count, ping
ping()  # falls back to stubs if offline — keep going either way

query("SELECT verdict, COUNT(*) n FROM mcp_server_registry GROUP BY verdict")
query("SELECT * FROM mcp_server_registry ORDER BY trust_score ASC LIMIT 20")   # watchlist
query("SELECT * FROM mcp_server_registry ORDER BY trust_score DESC LIMIT 10")  # leaderboard
query("SELECT * FROM mcp_server_registry WHERE risk_tier='CRITICAL'")
```

`zobridge.py` defaults to `http://127.0.0.1:8772` and falls back to stubs on connection failure. Note: the mock's SQL parser is substring-pattern, so `COUNT(*)` / `GROUP BY` / `ORDER BY` may return raw rows rather than aggregates — do aggregation in Python after `query()`, or extend the mock's matcher.

## Server management

```python
import subprocess, time, os, sys, pathlib, httpx

def start_server():
    # Windows: prefer run_local.ps1. On POSIX, this kills+restarts directly.
    if os.name == "nt":
        subprocess.Popen(["powershell", "-NoProfile", "-File", ".\\run_local.ps1"])
    else:
        subprocess.run("pkill -f ui_server.py", shell=True); time.sleep(1)
        pathlib.Path("logs").mkdir(exist_ok=True)
        subprocess.Popen(["python", "ui_server.py"],
                         stdout=open("logs/ui_server.log", "a"),
                         stderr=subprocess.STDOUT)
    for _ in range(30):
        try:
            if httpx.get("http://localhost:8790/healthz", timeout=2).status_code == 200:
                print("Server up"); return
        except Exception: pass
        time.sleep(1)

def restart_server(): start_server()
```

## Session logging

After each improvement, append to `SESSION_LOG.md`:

```python
import pathlib, datetime
def log_improvement(what, routes_affected=None):
    entry = f"\n## {datetime.datetime.utcnow().isoformat()}Z\n{what}\n"
    if routes_affected: entry += f"Routes: {routes_affected}\n"
    pathlib.Path("SESSION_LOG.md").open("a", encoding="utf-8").write(entry)
```

## Directive emission

For anything too complex to fix locally (needs ZoComputer-side changes), emit a builder directive:

```python
import json, datetime, pathlib
def emit_directive(key, goal,
                   output_path="/home/workspace/zo_sentinel/ui_server.py",
                   constraints=None, criteria=None):
    d = {"directive_id": key, "goal": goal, "output_path": output_path,
         "constraints": constraints or ["Idempotent", "No regression"],
         "success_criteria": criteria or ["Renders correctly", "No 500s"],
         "source": "claude_code",
         "generated_at": datetime.datetime.utcnow().isoformat()}
    out = pathlib.Path("directives/pending") / f"{key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=2))
    print(f"Directive emitted: {out}")
```

## Tests

```bash
python tests/smoke_audit.py   # boots-then-probes; waits on /healthz
python treewalk.py            # full audit -> treewalk_report.md + directives/pending/
```

CI (`.github/workflows/audit.yml`) runs `smoke_audit.py` on every PR + push to `main`.

## Architecture constraints

Non-obvious rules from `docs/ARCHITECTURE.md` — violating them breaks the surrounding system:

- **WriteService is the sole state bus.** Never add a direct DuckDB import. All persistence flows through `ws_write` / `ws_query` / `ws_execute`.
- **No external HTTP** from this code. Only `inference_router :8773` is allowed outbound.
- **Append-only tables.** `mcp_server_registry`, `mcp_signal_scores`, `mcp_threat_associations`, `audit_log` reject UPDATE/DELETE. Express state changes as new inserts.
- **Signal invariant contract.** Any signal write must include `signal_type`, `confidence` (0.0–1.0), `evidence_blob`, `server_id`, `scored_at` (ISO 8601).
- **30s heartbeat thread** in both services — writes `service_health.last_heartbeat`. Don't remove it.
- **Single-instance lock** at `tempfile.gettempdir()/<service>.pid`.
- **Catch-all exception middleware** → routes degrade to JSON with empty rows rather than 500. Keep this.
- **Security headers middleware** sets CSP / X-Frame-Options / Referrer-Policy / X-Content-Type-Options on every response.

## Directive-builder incident

`docs/INCIDENT_2026-05-09.md` — the day the directive-builder overwrote `ui_server.py` with a 9.8 KB stub with no backup. Hard constraints from the postmortem:

1. Never write directly to a deployment path. Builder must clone-branch-PR.
2. Backup before rewrite — snapshot to `<file>.bak.<utc-iso>`.
3. Patch ≠ rewrite — different handlers, different code paths. Rewrites need `--allow-rewrite` + backup confirmation.
4. Size-delta sanity check — large collapse requires operator confirmation.
5. Templates in `templates/` are legacy Jinja kept verbatim for parity — `ui_server.py` inlines its own HTML/CSS/JS. ZO MESH's `dashboard.html` belongs in a separate repo.

Directives go in `directives/` (timestamped, flat) or `directives/pending/` (queue for builder) as JSON with `source`, `summary`, `id`, `suggested_fix`, `evidence`, `category`, `details`, `observed_at`, `output_file`, `severity`.

## Verdicts / signals reference

- **Documented vocabulary** (in fixtures + arch docs): `TRUSTED_GENERAL`, `TRUSTED_RESEARCH`, `ENTERPRISE_CONTROLLED`, `CAUTION_LIMITED`, `HIGH_RISK_ISOLATED`, `KNOWN_THREAT`, `INSUFFICIENT`.
- **Production-emitted** (per live `mcp_server_registry`): `unknown` (~82%), `TRUSTED_RESEARCH` (~16%), `ENTERPRISE_CONTROLLED`, `CAUTION_LIMITED`, NULL. There is real vocabulary drift between the docs and the synthesiser.
- **Aliased input vocabulary** for `/api/servers?verdict=`: `TRUSTED` → TRUSTED_*; `UNTRUSTED` → HIGH_RISK_ISOLATED + KNOWN_THREAT; `AMBER` → CAUTION_LIMITED + ENTERPRISE_CONTROLLED; `UNKNOWN` → INSUFFICIENT + lowercase `unknown` + NULL. Exact verdict names also accepted.
- **Canonical signal types** (enforced by trust_synthesiser): `supply_chain_score`, `domain_trust_score`, `community_signal`, `temporal_stability`, `permission_scope_score`, `tool_description_safety`, `injection_resilience`, `evidence_density`, `registry_breadth`, `context_efficiency`, `vendor_concentration`, `traffic_fingerprint`.

## Rules of engagement

- Fix stability first (P0), then build features (P1→P6).
- Restart server after every `ui_server.py` change.
- Re-run `treewalk.py` after every set of changes.
- Append every improvement to `SESSION_LOG.md`.
- Emit a directive for anything needing ZoComputer-side changes.
- Stubs are acceptable — keep moving, don't block on live data. EXCEPT where a stub would shadow an existing implementation — see "Import-boundary incident (2026-08-16)", constraint 3.
- No permission prompts. No confirmation pauses. Keep iterating.

## Builder self-repair harness (ZoComputer-side)

Cross-host operational work, distinct from the UI above. These daemons run under
`/home/workspace/` (supervisord), not in this repo — build them via `emit_directive` with a remote
`output_path`. Directives are staged in
`…\ZoComputer\shared\code\zo_sentinel\SESSION_LOG_harness.md` and synced with `append_harness.ps1`.

Motivation — failures already seen in production: the directive generator died and went 23 days
unnoticed; a builder sort crash (mixed `str`/`float`) ran for hours; an empty directive queue caused
Ollama fallback loops; nothing alerted on any of it.

**Three components:**

1. **`process_watchdog.py`** — ZoComputer daemon, 60s interval. Monitors `zo_sentinel_builder.py`,
   `sentinel_directive_generator.py`, `auto_promoter.py`, `mcp_directory_ingestor.py`; on death,
   restart + emit a `mesh_event`. Log restarts to `/home/workspace/logs/watchdog_restarts.log`;
   heartbeat to `service_health` every 30s; add to `/etc/zo/supervisord-user.conf` with
   `autorestart=true`.
2. **`builder_health_checker.py`** — Tower dissolvable, every 15 min via ZoWarmWorker. Reads the tail
   of `zo_sentinel_builder.log` (via `shared/outputs/`). If any of `Cycle error`, `weights evicted`,
   `task: unknown`, `IsADirectory` appears >3× in the last 20 lines, write
   `shared/outputs/builder_alert.json` for `process_watchdog` to act on.
3. **`directive_queue_guardian.py`** — ZoComputer daemon, every 10 min. Checks
   `mesh_memory WHERE agent_id='zo_sentinel.directive'`; if count < 3, trigger the generator
   (`pkill -SIGUSR1` or restart). Restart the generator or builder if down. Log to
   `/home/workspace/logs/queue_guardian.log`; add a supervisord `[program:...]` block.

**Failure patterns to detect:**

| Pattern | Meaning | Action |
|---|---|---|
| `Cycle error` in builder log | builder crash | restart builder |
| `weights evicted` >5× in 10 min | MINIMAX missing | run `key_hydrator` |
| `task: unknown` | directive schema issue | log + alert |
| Queue depth 0 for >30 min | directive generator stale | restart generator |
| Generator log not updated >1 hr | process dead | restart generator |

## Scope boundary

In scope: discovery, signal collection, trust synthesis surfacing, threat ingest, verdict generation, audit logging, analyst workflows, the dashboard.

Out of scope (handled externally — don't add): HTTP gateway/proxy, network-layer blocking, runtime security (WAF/IDS/RASP), user auth/RBAC, secret/vault management, firewall rules, container orchestration.

## Import-boundary incident (2026-08-16)

Three-day total build outage. A directive mutated `zo_sentinel/__init__.py` — a file whose own
docstring says it must stay a bare package marker — into an eager import hub pulling in `app.db`.
A second edit added `from app.routers import api_router` to `app/__init__.py`, pointing at an
untracked `app/routers/` indexing ~35 modules that were never written. Every
`python3 -m zo_sentinel.*` entrypoint then died at package init. Neither edit was ever committed.

Hard constraints from this postmortem:

1. **`zo_sentinel/__init__.py` imports nothing.** It is a package marker only — it exists to pin
   resolution to the local checkout. Never add imports to it.
2. **Nothing under `zo_sentinel/` may import `app`.** The build pipeline must not depend on the
   deployed FastAPI package. A broken route must never be able to starve the builder.
3. **Do not stub missing router modules.** `app/routers/__init__.py` indexes an
   `app/router_<name>.py` convention that does not match disk; the real implementations live in
   `app/api/<name>.py` and `services/staged/<name>/router.py`. Empty routers would shadow working
   code and fail silently — strictly worse than the crash. This overrides "stubs are acceptable".
4. **A restart is not a recovery.** Any supervisor that restarts a daemon must verify it is still
   alive ~10s later. The watchdog restarted the promoter for three days while it died at import
   every time.
5. **Leave no uncommitted edits to tracked files.** Modifications outside a directive's declared
   target set are a gate failure. Both files here sat modified-but-uncommitted for three days.

Open follow-ups: reconcile `app/routers/` against the real layout (there is also an `app/routers.py`
shadowed by the package); wire `tests/test_promotion_blocks_unshippable_import_path.py` into the
pre-merge gates and extend it to assert constraints 1 and 2; alarm when `pending/` is non-empty and
no `.done.json` has been written for >2h.
