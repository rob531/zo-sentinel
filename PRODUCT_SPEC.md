# ZO-SENTINEL Product Specification — v1.0 Target

*Context-optimized spec. Version: 4, 2026-04-22. Target length: ~200 lines.*
*This document is read by the directive generator every cycle.*
*Keep it dense, keep it true, cut anything that duplicates the DuckDB schema.*

---

## 1. Product Vision & Core Loop

**Goal:** An autonomous pipeline that assesses Model Context Protocol (MCP)
servers, enriches them with threat intelligence, and assigns a deterministic
security verdict.

**Primary User:** A CISO / Security Architect looking up an MCP by name in
the Search-Driven UI (port 8790) to make a deployment decision.

**Scope posture (non-negotiable):** Sentinel is an **intelligence layer**.
It produces trust signals, verdicts, and detection artefacts that other
systems (gateways, portals, enforcement planes) consume. Sentinel does
NOT route MCP traffic, mediate client↔server calls, authenticate users,
enforce policy at call time, or block/throttle traffic. See
`SENTINEL_SCOPE_BOUNDARY.md` for the decision rules; section 9 below for
the static out-of-scope list.

**The Loop:**
1. `mcp_scanner` ingests raw MCPs from public registries.
2. `signal_analyser` + enrichment modules gather 7 signals per MCP.
3. `trust_synthesiser` computes a weighted composite and assigns a verdict.
4. `attestation_engine` writes a non-binding attestation.
5. `threat_intel_ingestor` + `risk_ranker` overlay external threat data.
6. The UI + external API (port 8791) surface all of it.

---

## 2. Verdict Taxonomy (6 tiers + INSUFFICIENT)

These are the ONLY valid verdicts. Do not propose new tiers or collapsing
existing ones. The synthesiser is calibrated for these exact states.

- **TRUSTED_GENERAL**      — composite >75, all signals present. Approved for general enterprise use.
- **TRUSTED_RESEARCH**     — composite >60. Safe for research / exploratory use.
- **ENTERPRISE_CONTROLLED**— composite >45. Acceptable with documented security controls.
- **CAUTION_LIMITED**      — composite >30. Requires additional review.
- **HIGH_RISK_ISOLATED**   — composite >15. Sandboxed environments only.
- **KNOWN_THREAT**         — composite <=15 or matched known-threat signal.
- **INSUFFICIENT**         — >=5 of 8 signals missing. Not a risk tier; a data-gap state.

---

## 3. Signal Model (the hard contract)

Eight signals feed the composite: `domain_trust`, `tool_description_safety`,
`permission_scope`, `supply_chain`, `community_signal`, `temporal_stability`,
`supply_chain_enrichment`, `community_signal_enrichment`.

**Signal Invariant — non-negotiable:** every signal producer must write rows
to `mcp_signal_scores` or `mcp_signal_enrichments` whose evidence blob matches:
```
{"signal_type": "<snake_case_name>", "confidence": 0.0-1.0, "evidence_blob": {...}}
```
Any new signal / enricher that does not emit this shape is invalid. The
inference engine cannot weight non-conforming signals.

**Enricher contract:** pure function `compute_score(metadata: dict) -> (float in [0,100], evidence dict)`.
No DB writes, no network, no imports of protected modules. Evaluated by
`enrichment_harness.py` against a synthetic corpus; rejected if it yields
fewer than 20 distinct scores across 34 fingerprints.

---

## 4. Data Contracts & State Management

- **Single source of truth:** DuckDB (one main database). All cross-daemon
  state lives there. Generator can introspect live schema via
  `information_schema.columns` if it needs column detail — **do not duplicate
  column definitions in this document**.
- **Immutability:** Existing rows in `mcp_server_registry` are append-only
  or update-in-place for verdicts. Daemons MUST NOT execute `DROP` or
  `DELETE` on core tables (`mcp_*`).
- **Retention:** raw `evidence_blob` columns expire at 30 days. Verdicts,
  attestations, threat associations, and risk register entries are retained
  indefinitely.
- **Freshness SLAs:**
  - New MCP must have a first verdict within **24h** of `first_seen`.
  - Every live MCP must be re-verdict-ed within **7 days** of its previous
    `last_assessed`.
  - `stale_data_cleaner` and `assessment_scheduler` enforce these windows.
- **Awaiting-user tables:** `mcp_submissions`, `mcp_exemptions`, `mcp_decisions`,
  `mcp_policy_rules`, `mcp_fingerprints`, `mcp_tool_hashes` are legitimately
  empty until user/admin action populates them. Do NOT propose pipeline
  changes to "fix" their emptiness.

---

## 5. Inter-Daemon Communication (critical architectural rule)

**No HTTP between peer daemons.** All state exchange goes through
`write_service` on `:8772`. Daemons talk to `write_service`, never directly
to each other.

- Write path: `POST :8772/write {table, rows, wait}`.
- Read path: `POST :8772/query {sql, params}` (parameterized).
- Admin path: `POST :8772/execute {sql, wait}` (DDL / DML).
- Polling pattern: daemons find work via `SELECT ... WHERE verdict IS NULL`
  or equivalent pending-state queries, not by receiving messages.

`inference_router` on `:8773` is the one exception — daemons call it directly
for LLM inference. That is infrastructure, not peer state exchange.

---

## 6. Execution & Failure Contracts

- **Timeouts:** external I/O (HTTP scraping, third-party APIs) must have a
  10s strict timeout. Internal write_service calls: 30s max.
- **Graceful degradation:** on write_service 5xx or timeout, implement
  exponential backoff (3 tries). Heartbeat must still fire even if the
  work cycle fails.
- **Heartbeat contract:** every daemon writes to `service_health` at least
  every 60s. Stale > 2h = considered dead.
- **Dependency declaration:** daemons declare external libraries with top
  comment `# deps: requests, duckdb, ...` so `auto_dependency_resolver`
  can parse them before smoke.
- **No blocking startup:** daemons must start uvicorn / enter the main
  loop within 10s of process start. Long-running init goes in a background
  thread after liveness is declared.

---

## 7. Security & Trust Boundaries

- **Secrets:** no hardcoded tokens. API keys via `os.environ.get()`. Keys
  file for external API is mode 0600, one key per line, comments allowed.
- **External API surface:** `:8791` is read-only for v1.0. Authenticated
  via `X-API-Key` header, rate-limited 60 req/min/key.
- **Internal API surfaces:** `:8790` UI, `:8780` approval_workflow,
  `:8781` registry_api, `:8792` pi_flagged_review_api, `:8795` build_watcher.
  All bind localhost unless explicitly exposed via ZoComputer zite.
- **SQL injection:** all user-supplied values go through `params` arrays
  to `write_service /query`, never interpolated into SQL strings.
- **Audit:** every admin-write action (exemption create, attestation revoke,
  manual verdict override) inserts a row in `audit_log`.

---

## 8. Naming & Idempotency Invariants

- File names match import names exactly. `foo_bar.py` is imported as `foo_bar`.
- NEVER prefix an output filename with `build_` or `rebuild_` (those
  belong on task names only).
- NEVER suffix a filename with `_v3`, `_v4`, `_final`, `_new`. Use `_v2`
  ONCE for a deliberate, documented rewrite.
- Files listed in `sentinel_directive_generator.PROTECTED_FILES` are
  hand-calibrated. To change behavior, propose a NEW companion module,
  never a rewrite.
- Never propose a file whose name already appears in `ALREADY_BUILT`.

---

## 9. Strictly Out of Scope for v1.0

Proposals touching any of these MUST be rejected:

- Multi-tenancy / org RBAC / OAuth flows (only `X-API-Key` is permitted).
- Billing, metering, plans.
- Slack / Teams / email integrations (`notification_hub.py` stays dark).
- Grafana / Prometheus dashboards.
- GraphQL surface (`graphql_schema_builder.py` is dormant; do not wire it).
- Outbound webhooks to third parties (`incident_webhook_dispatcher.py` dormant).
- Any ServiceNow outbound; only inbound webhook is in scope.
- Retention DELETE daemons (we expire by query filter, not by row deletion).
- ML-based anomaly detection beyond existing `pattern_learner.py`.
- **Any MCP gateway / proxy / portal / enforcement-plane functionality.**
  Sentinel does not sit between MCP client and MCP server at runtime. Any
  directive that proposes traffic routing, call-time policy enforcement,
  tool-call blocking, or authentication mediation is out of scope. The
  market is saturated with gateway/portal products (Cloudflare MCP portals,
  CS AiDR, Kong, etc.) — Sentinel complements them with intelligence, it
  does not replicate them.

---

## 10. Directive Generator Execution Rules

On every cycle, compare this spec to the live wiring map + gaps map (supplied
alongside this document in the prompt). Propose directives that:

1. Build a daemon / module the gaps map flags as missing AND the spec
   explicitly names in sections 1–7 or appendix A/B.
2. Close a freshness-SLA gap (section 4) by fixing or extending the
   scheduler, not by proposing new schedulers.
3. Close an integration gap (ServiceNow inbound, AiDr commit, GitHub PR)
   per section 1's core loop.
4. Add the missing companion module when a protected file's behavior needs
   to change (per section 8).
5. Self-correct a failing daemon by conforming its output to the signal
   invariant (section 3) or inter-daemon rule (section 5).
6. Adopt published detection artefacts (Appendix B) into the intelligence
   layer — regex fingerprints, JSON-RPC method markers, shadow-MCP
   indicators. These are library modules, not daemons; they produce signals
   or detection outputs consumed by existing pipelines.

Reject any proposal that:
- Targets a PROTECTED or ALREADY_BUILT file directly.
- Violates a naming invariant (section 8).
- Falls under section 9 (out of scope).
- Introduces HTTP between peer daemons (section 5).
- Proposes a new verdict tier, new signal shape, or DROP/DELETE on core tables.
- Crosses the intelligence/enforcement boundary (section 1 scope posture).

---

## Appendix A — Directive Candidates (concrete target list)

Concrete v1.0 gaps. These filenames do not yet exist and are legitimate
targets for the directive generator. Evaluate against live wiring/gaps map
before proposing; some may have been built since this list was last updated.

**Retention / lifecycle daemons (NOT YET BUILT):**
- `retention_sweeper.py` — age-based expiry for evidence_blob columns (30d)
- `exemption_expirer.py` — nightly check for exemptions past valid_until
- `attestation_refresher.py` — regenerate attestations approaching expiry

**Admin UI surfaces (NOT YET BUILT on port 8790):**
- `admin_exemptions.html` — manage mcp_exemptions records
- `admin_policies.html` — manage mcp_policy_rules
- `admin_submissions.html` — triage pending MCP submissions (portal exists; admin view does not)
- `admin_attestations.html` — revoke / extend attestations

**Integration wiring (files exist, integration incomplete):**
- `snow_connector.py` — built 2026-04-16; needs wiring into approval_workflow
- `aidr_commit_gateway.py` — built 2026-04-17; needs verdict-check enforcement test
- `github_pr_checker.py` — built; needs webhook wiring

**Documentation (NOT YET BUILT):**
- `ARCHITECTURE.md` — inter-daemon topology, write_service contract, signal invariant
- `OPERATIONS.md` — supervisord layout, log locations, recovery runbook
- `sentinel_external_api.md` — external API reference (directive already queued)

**Testing (NOT YET BUILT):**
- `e2e_scenarios.py` — three canonical flows scripted: new MCP → signal scored
  → verdict → attestation → UI visible.

**Known-broken / needs repair:**
- `rug_pull_monitor` — heartbeat >14h stale; needs restart or service file review
  (prefer diagnosis directive over rebuild; file is protected)
- `write_service` self-heartbeat — service responsive but beat is >3h old;
  diagnostic-only, do NOT propose rebuild (write_service is protected)

---

## Appendix B — Detection Artefacts from Published Sources

Sources of record: Cloudflare enterprise MCP reference architecture
(blog.cloudflare.com/enterprise-mcp, 2026-04-14) publishes JSON-RPC method
signatures, protocol-version markers, and shadow-MCP detection patterns
that Sentinel can adopt as **library modules** (pure functions, no daemons,
no network, no DB writes). See `SENTINEL_SCOPE_BOUNDARY.md` for why these
are in-scope (detection artefacts = Q2 of the three-question rule).

**Library modules — detection/fingerprint primitives (NOT YET BUILT):**
- `mcp_traffic_fingerprints.py` — regex-based detection of JSON-RPC MCP
  traffic in HTTP bodies or log lines. Recognises methods: `initialize`,
  `tools/call`, `tools/list`, `resources/read`, `resources/list`,
  `prompts/list`, `prompts/get`, `sampling/createMessage`,
  `notifications/initialized`, `roots/list`, and the
  `"protocolVersion":"202[4-9]"` marker. Tolerates whitespace variation.
  Pure library: exports compiled patterns + `detect_mcp_methods`,
  `is_mcp_traffic`, `extract_session_indicators`. **Directive already queued
  2026-04-22.**
- `mcp_tool_schema_patterns.py` — pure library detecting architectural
  patterns in an MCP server's tool definitions: (a) progressive-disclosure
  ("Code Mode" shape — ≤4 high-level tools exposing dynamic discovery),
  (b) brute-force enumeration (≥20 tools with full schemas upfront),
  (c) hybrid. Output feeds a future `context_efficiency` signal — servers
  using progressive disclosure scale better in portal deployments and
  deserve positive signal weight. Pure function: takes a tool-definitions
  list, returns `{pattern: str, tool_count: int, evidence: dict}`.
- `shadow_mcp_indicators.py` — pure library providing URL-path and
  hostname-pattern indicators (`/mcp`, `/mcp/sse`, `mcp.*` subdomains,
  known MCP server hostnames like `mcp.stripe.com`, `mcp.cloudflare.com`)
  for log analysis. Complements `mcp_traffic_fingerprints` (which scans
  bodies); this one scans URLs/hostnames. Pure function, no I/O.

**Signal additions enabled by Appendix B modules (NOT YET BUILT, deferred):**
- `context_efficiency_enrichment.py` — consumes `mcp_tool_schema_patterns`
  output, scores progressive-disclosure servers higher than brute-force
  enumerators. Enrichment-contract compliant. **DEFERRED** until the three
  weak signals (`permission_scope`, `temporal_stability`,
  `tool_description_safety`) have moved off their current 3-distinct-values
  plateau. Do not propose until weak-signal work is visibly progressing.

**Wiring work (NOT YET BUILT):**
- `mcp_traffic_fingerprints` → consumed by `mcp_scanner` when scanning
  candidate server responses for MCP protocol confirmation. Directive to
  wire this comes AFTER the library module lands and smokes clean.
- `shadow_mcp_indicators` → consumed by future log-analysis ingestor (not
  yet scoped). Library module stands alone until an ingestor needs it.

**Rules specific to Appendix B work:**
1. Every module here is a **pure library**. No daemons. No network. No DB.
   No imports of protected modules. If a directive proposes a daemon or
   scheduled job in Appendix B's name, reject it.
2. Attribution is required in the module docstring. Source: "Cloudflare
   enterprise MCP reference architecture, blog.cloudflare.com, 2026-04-14."
3. These are *detection* artefacts. Any directive that adds blocking,
   redirecting, or traffic mediation based on these patterns is out of
   scope (section 9) and must be rejected.
4. Regex patterns must tolerate whitespace variation as published by
   Cloudflare (e.g. `"method"\s{0,5}:\s{0,5}"tools/call"`). Compile at
   module load; do not recompile per-call.
5. Each module must include an `if __name__ == '__main__'` self-smoke that
   exercises the detection functions against at least three known-good
   inputs and asserts non-empty results. `python3 <module>.py` exits 0 on
   success.


---

## Appendix C - Phase II/III/IV Sentinel App Capabilities (directive candidates, NOT YET BUILT)

*Source: SENTINEL_ROADMAP_v2.md + addendum. These are Sentinel APP modules (MCP threat-signal product logic) -- NOT builder infrastructure. Each line is a directive candidate the architect should propose. Enricher modules follow the section 3 signal contract: compute_score(metadata: dict) -> (float in [0,100], evidence dict with keys verdict and missing); no DB writes, no network, no imports of protected modules; a __main__ self-test that prints PASS.*

### Phase II - Directory ingestion (low-hanging, do first)
- directive candidate: `directory_presence_signal.py` -- enricher; compute_score reads directory_listings (curated directories the MCP appears in, e.g. the Anthropic reference list); presence in a gatekept directory is the strongest single positive trust signal; a missing field contributes 0 and is appended to evidence['missing']; returns (0..100, {verdict, missing}); __main__ self-test on a sample metadata dict asserts 0<=score<=100 and prints PASS.

### Phase III - Endpoint trust (the big one)
- directive candidate: `mcp_endpoint_extractor.py` -- pure library; extract_endpoints(files: dict) parses default-config sources (dotenv, typescript config, python settings modules) and README text for hostnames; returns list of {hostname, source, user_configurable}; stdlib + regex only, no DB/network; __main__ self-test over 3 sample configs asserts non-empty and prints PASS.
- directive candidate: `endpoint_trust_signal.py` -- enricher; compute_score reads endpoints (list of {hostname, tier in FIRST_PARTY|THIRD_PARTY|UNKNOWN}); composite over the tier distribution (all FIRST_PARTY = high, any UNKNOWN = lower); section 3 contract; __main__ prints PASS.
- directive candidate: `operator_identity_signal.py` -- enricher; compute_score reads operator ({name, corporate, has_dpa}); strongest trust when endpoints resolve to a named corporate operator with a DPA on file; section 3 contract; __main__ prints PASS.
- directive candidate: `data_residency_signal.py` -- enricher; compute_score reads endpoint_geos (ISO country codes where endpoints resolve); scores data-residency risk; section 3 contract; __main__ prints PASS.

### Phase IV - Negative signals (dominant; override positive)
- directive candidate: `threat_intel_endpoint_matcher.py` -- pure library; match_endpoints(endpoints: list, feeds: dict) checks each endpoint hostname against threat-intel feed sets; any match returns {blocked: True, feed, hostname}; negative signals dominate positive; stdlib only; __main__ over a synthetic feed asserts a known-bad match and prints PASS.
- directive candidate: `domain_provenance_signal.py` -- enricher; compute_score reads domain_age_days, registrar, typosquat_distance; lower score for young domains, sketchy registrars, or small typosquat distance to a popular package; section 3 contract; __main__ prints PASS.


---

## Appendix D - 3-Tier App Foundation (directive candidates, NOT YET BUILT)

*The MVP app-surface for the 3-tier SaaS target (see sentinel_3tier_target_spec). Built on the CURRENT stack: FastAPI + data via write_service:8772 (DuckDB) -- NOT Postgres yet (alpha candidates stay DuckDB until the staged migration). Each is a directive candidate; enricher rules do not apply -- these are app modules with their own self-tests.*

### Tier-1 App Foundation
- directive candidate: `tenant_org_model.py` -- org/tenant model: create_org(name)->org_id, add_member(org_id,user_id,role), and org_scope(sql,org_id) that injects an org_id filter so every product query is row-scoped. Persist via write_service /write; never import duckdb. ACCEPTANCE: __main__ creates an org, adds a member, asserts org_scope injects the filter, prints PASS.
- directive candidate: `oauth_login_service.py` -- OAuth social login + JWT sessions: begin_oauth(provider)->url, complete_oauth(provider,code)->(user,jwt), verify_jwt(token)->claims, issue_session(user_id)->jwt. Stdlib + PyJWT; no network in the self-test. ACCEPTANCE: __main__ issues a jwt for a fake user and asserts verify_jwt round-trips user_id, prints PASS.
- directive candidate: `rbac_enforcer.py` -- roles {admin,member} + a require_role(role) FastAPI dependency that 403s on insufficient role + has_permission(role,action). ACCEPTANCE: __main__ asserts require_role('admin') rejects a member and allows an admin, prints PASS.
- directive candidate: `verdict_breakdown_api.py` -- FastAPI GET /servers/{id}/verdict: read mcp_llm_axis_scores via write_service /query, return per-axis {label,p_top} for the 6 axes + overall_risk, the verdict tier, a rule-override layer (a CRITICAL axis forces the tier), and a criteria_version string. ACCEPTANCE: __main__ on a sample axis-score dict asserts the override forces the expected tier and criteria_version is present, prints PASS.

### Tier-1 App Surface
- directive candidate: `org_entity_search_api.py` -- FastAPI GET /servers (org-scoped, filter by verdict_tier/name/risk) + GET /servers/{id}; reads mcp_server_registry via write_service /query scoped by org_id. ACCEPTANCE: __main__ asserts the filter builds org-scoped SQL and returns a list shape, prints PASS.
- directive candidate: `overview_dashboard_api.py` -- FastAPI GET /dashboard/overview: verdict-tier distribution (counts per tier), total servers, recent scans, a 7-day trend; org-scoped via write_service. ACCEPTANCE: __main__ on sample rows asserts the tier-distribution dict sums to the row count, prints PASS.
- directive candidate: `entity_report_exporter.py` -- per-server report export: render_report(server_id)->html at a stable shareable path + export_csv(server_ids)->bytes. No network. ACCEPTANCE: __main__ renders a report for a sample server and asserts the html contains the verdict tier + all 6 axes, prints PASS.
- directive candidate: `verdict_watchlist_service.py` -- watchlist: add_watch(org_id,user_id,server_id) + on_verdict_change(server_id,old,new) that queues a notification to watchers when a watched server changes tier. Persist via write_service. ACCEPTANCE: __main__ adds a watch, simulates a tier change, asserts a notification is queued, prints PASS.

### Tier-1 App Platform
- directive candidate: `org_api_key_manager.py` -- per-org API keys: issue_key(org_id)->key (store only a hash), revoke_key(key_id), verify_key(key)->org_id, and a per-key rate-limit check. Via write_service. ACCEPTANCE: __main__ issues a key, verifies it resolves the org, revokes it, asserts verify then fails, prints PASS.
- directive candidate: `verdict_webhook_dispatcher.py` -- outbound webhooks: register_webhook(org_id,url,events) + dispatch(event,payload) that POSTs to registered urls (Slack-formatted for slack urls). Inject a fake poster in the self-test. ACCEPTANCE: __main__ registers a webhook, dispatches a verdict_change to a fake poster, asserts delivery, prints PASS.
- directive candidate: `product_audit_log.py` -- append-only audit log: record(org_id,actor,action,target) writing an audit_log row via write_service + query_audit(org_id,filters) for admins; insert-only/immutable. ACCEPTANCE: __main__ records a login + a role_change, queries them back org-scoped, asserts order + immutability, prints PASS.
