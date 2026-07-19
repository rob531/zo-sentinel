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

- File names match import names exactly (a module's import name is its stem).
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

## Appendix B - [DEPRECATED 2026-06-24] detection / enumeration / signal primitives

**Not build targets.** Detection primitives, endpoint enumerators, and signal additions are the
old feature-extraction approach the SFT student model replaced. Do not propose or build them.
The live build queue is Appendix E (the 3-tier app surface).

## Appendix C - [DEPRECATED 2026-06-24] enricher / signal candidates

**Not build targets.** The SFT student model (v3.0; 6 risk axes -> mcp_llm_axis_scores) now owns
risk scoring, so the hand-built enricher/signal modules here are REDUNDANT. Do not propose or
build them. See Appendix E for the live build queue.

## Appendix D - 3-Tier App Foundation (directive candidates, NOT YET BUILT)

*The MVP app-surface for the 3-tier SaaS target (see sentinel_3tier_target_spec). Built on the CURRENT stack: FastAPI + data via write_service:8772 (DuckDB) -- NOT Postgres yet (alpha candidates stay DuckDB until the staged migration). Each is a directive candidate; enricher rules do not apply -- these are app modules with their own self-tests. INTEGRATIONS / external connectors (webhooks, Slack, SIEM) are OUT OF SCOPE -- they need per-customer credential segmentation (the deferred external-client-auth branch; see the PARKED snow/aidr rule). PUBLIC API endpoints (including report/data endpoints) ARE in scope.*

### Tier-1 App Foundation
- directive candidate: `tenant_org_model.py` -- org/tenant model: create_org(name)->org_id, add_member(org_id,user_id,role), and org_scope(sql,org_id) that injects an org_id filter so every product query is row-scoped. Persist via write_service /write; never import duckdb. ACCEPTANCE: __main__ creates an org, adds a member, asserts org_scope injects the filter, prints PASS.
- directive candidate: `oauth_login_service.py` -- OAuth social login + JWT sessions: begin_oauth(provider)->url, complete_oauth(provider,code)->(user,jwt), verify_jwt(token)->claims, issue_session(user_id)->jwt. Stdlib + PyJWT; no network in the self-test. ACCEPTANCE: __main__ issues a jwt for a fake user and asserts verify_jwt round-trips user_id, prints PASS.
- directive candidate: `rbac_enforcer.py` -- roles {admin,member} + a require_role(role) FastAPI dependency that 403s on insufficient role + has_permission(role,action). ACCEPTANCE: __main__ asserts require_role('admin') rejects a member and allows an admin, prints PASS.
- directive candidate: `verdict_breakdown_api.py` -- FastAPI GET /servers/{id}/verdict: read mcp_llm_axis_scores via write_service /query, return per-axis {label,p_top} for the 6 axes + overall_risk, the verdict tier, a rule-override layer (a CRITICAL axis forces the tier), and a criteria_version string. ACCEPTANCE: __main__ on a sample axis-score dict asserts the override forces the expected tier and criteria_version is present, prints PASS.

### Tier-1 App Surface
- directive candidate: `org_entity_search_api.py` -- FastAPI GET /servers (org-scoped, filter by verdict_tier/name/risk) + GET /servers/{id}; reads mcp_server_registry via write_service /query scoped by org_id. ACCEPTANCE: __main__ asserts the filter builds org-scoped SQL and returns a list shape, prints PASS.
- directive candidate: `overview_dashboard_api.py` -- FastAPI GET /dashboard/overview: verdict-tier distribution (counts per tier), total servers, recent scans, a 7-day trend; org-scoped via write_service. ACCEPTANCE: __main__ on sample rows asserts the tier-distribution dict sums to the row count, prints PASS.
- directive candidate: `entity_report_exporter.py` -- per-server report export: render_report(server_id)->html at a stable shareable path + export_csv(server_ids)->bytes. No network. ACCEPTANCE: __main__ renders a report for a sample server and asserts the html contains the verdict tier + all 6 axes, prints PASS.
- directive candidate: `verdict_watchlist_service.py` -- watchlist: add_watch(org_id,user_id,server_id) + on_verdict_change(server_id,old,new) that queues an in-app + email notification to watchers (no external connectors) when a watched server changes tier. Persist via write_service. ACCEPTANCE: __main__ adds a watch, simulates a tier change, asserts a notification is queued, prints PASS.

### Tier-1 App Platform
- directive candidate: `org_api_key_manager.py` -- per-org API keys: issue_key(org_id)->key (store only a hash), revoke_key(key_id), verify_key(key)->org_id, and a per-key rate-limit check. Via write_service. ACCEPTANCE: __main__ issues a key, verifies it resolves the org, revokes it, asserts verify then fails, prints PASS.
- directive candidate: `product_audit_log.py` -- append-only audit log: record(org_id,actor,action,target) writing an audit_log row via write_service + query_audit(org_id,filters) for admins; insert-only/immutable. ACCEPTANCE: __main__ records a login + a role_change, queries them back org-scoped, asserts order + immutability, prints PASS.

## Appendix E - Tier-2 App Milestones (directive candidates, NOT YET BUILT)

*Added 2026-06-24. The SFT student model owns risk scoring (Appendix B/C deprecated). The live build
queue is the 3-tier SaaS app surface (see sentinel_3tier_target_spec.md + the app/ skeleton now on
main): wire the SFT scores into the product, then the presentation tier. Routing: backend `.py` ->
the webapp_backend_fastapi recipe; view `.html` -> the webapp_frontend_react recipe. GOAL: a LIVE MVP.*

**Scoring -> product (consume the SFT axis scores from Postgres):**
- directive candidate: `trust_gating_override.py` -- deterministic verdict override (SSL-Labs criticalFailure/override pattern) used by verdict_view_api/app_scoring_consumer: when maintainer_trust=ESTABLISHED OR the registry_source/url is an allow-listed OFFICIAL org (the official MCP registry, or verified big-tech GitHub orgs e.g. microsoft/google/googleapis/stripe/supabase/cloudflare/docker/anthropic/aws), CAP overall_risk so it is NEVER HIGH/CRITICAL and reframe as 'high-capability, trusted'. Inputs: the 6-axis labels + registry_source/url; output: the adjusted tier + the reason. ACCEPTANCE: __main__ asserts a Stripe-like {maintainer_trust:ESTABLISHED, capability_breadth:BROAD, data_sensitivity:CRITICAL} no longer yields HIGH, and an unknown-maintainer broad-capability server still does; prints PASS. RATIONALE: fixes the false-positive audit (2026-06-25) where official Microsoft/Stripe/Google-Cloud MCPs were labelled HIGH because overall_risk conflates inherent surface with threat.
- directive candidate: `app_scoring_consumer.py` -- data-access layer: given a server_id, read mcp_llm_axis_scores -> {axis: {label, p_top}} + overall_risk -> a risk_tier string; Postgres-portable SQL via the app DB session. The single seam the verdict/dashboard APIs read.
- directive candidate: `verdict_view_api.py` -- FastAPI router GET /servers/{server_id}/verdict: real per-axis breakdown + overall + risk_tier + a criteria_version string (SSL-Labs weighted-axes/override pattern), reading via app_scoring_consumer. REPLACES the hollow verdict_breakdown_api stub.
- directive candidate: `dashboard_summary_api.py` -- FastAPI router GET /dashboard/summary: verdict-tier distribution + scored/total counts + recent-scored list, reading mcp_llm_axis_scores + mcp_server_registry.

**Presentation tier (self-contained dashboards that fetch the REST API; no CDN/localStorage, a11y):**
- directive candidate: `overview_dashboard_view.html` -- verdict-tier distribution + counts + recent scans; fetches /dashboard/summary.
- directive candidate: `entity_detail_view.html` -- single-server detail: per-axis risk breakdown + overall tier; fetches /servers/{id}/verdict.
- directive candidate: `registry_search_view.html` -- org-scoped, filterable server list with risk tier; fetches the org_entity_search API.

**App assembly (make the service serve the full surface):**
- directive candidate: `app_router_registry.py` -- one include_app_routers(app) helper that mounts every app router (auth, rbac, verdict_view_api, dashboard_summary_api, org_entity_search_api, entity_report_exporter, ...) so app/main.py exposes the full API surface in a single call.

## Appendix F - v1.1 "Perspectives" (directive candidates, NOT YET BUILT)

*Added 2026-07-02 (chairman + council 2026-06-27 ruling: Perspectives is the first post-launch
differentiator). Perspectives = deterministic, admin-built faceted hierarchies / saved filters over
the scored corpus (65,532 servers x 7 axes). Reproducible, governable, ZERO per-query LLM cost, and
the natural attach point for trust-diff notifications ("alert me when anything in MY view changes
tier"). The facet universe is derived ONLY from real columns: mcp_server_registry.risk_tier /
verdict / registry_source / trust_score, and mcp_llm_axis_scores (axis_name, label) for the 7 axes
(overall_risk, auth_strength, capability_breadth, data_sensitivity, network_egress,
maintainer_trust, exploit_surface). NO invented facets (hosting-model / data-residency are OUT until
real columns exist). Routing: backend `.py` -> webapp_backend_fastapi recipe; view `.html` ->
webapp_frontend_react recipe. All persistence via write_service or the app DB session -- never
import duckdb, never invent CSV files.*

**Facet foundation:**
- directive candidate: `facet_enum_service.py` -- the deterministic facet universe: DISTINCT
  risk_tier, verdict, registry_source (+ trust_score quartile bands) from mcp_server_registry and
  DISTINCT (axis_name, label) pairs from mcp_llm_axis_scores (latest model_version only), each with
  row counts, read via the app_scoring_consumer seam (Postgres-portable SQL); returns
  {facet_key: [{value, count}]} where facet_key in {risk_tier, verdict, registry_source,
  trust_band, axis:<axis_name>}. Pure read, in-memory TTL cache. ACCEPTANCE: __main__ on sample
  rows asserts the dict shape, that axis facets are keyed axis:<name>, and per-facet counts sum to
  the sample row count; prints PASS.
- directive candidate: `perspective_model.py` -- saved-perspective persistence: rows
  {id, org_id, name, description, facet_filters (JSON), created_by, created_at, updated_at} in a
  `perspectives` table via write_service /write; create_perspective/get/list_for_org/update/delete,
  plus validate_facet_filters(filters, enums) that REJECTS unknown facet keys or values (enums =
  the facet_enum_service dict, passed in -- no network in the self-test). ACCEPTANCE: __main__
  creates a perspective, validates a good filter, asserts an unknown facet key AND an unknown value
  are both rejected; prints PASS.

**Query + admin surface:**
- directive candidate: `perspective_query_api.py` -- FastAPI GET /perspectives/{id}/servers:
  compile the saved facet_filters into ONE parameterized SQL query over mcp_server_registry joined
  to latest-model mcp_llm_axis_scores (via the app_scoring_consumer seam), org-scoped (org_scope
  pattern), paginated, and return {servers, total, facet_counts} where facet_counts gives the
  drill-down counts for each remaining facet. Fully deterministic -- zero LLM. ACCEPTANCE:
  __main__ compiles a sample filter {risk_tier:[HIGH], axis:auth_strength:[WEAK]} and asserts the
  generated SQL contains both predicates and parameter binding (no string interpolation of
  values), and the response shape has servers/total/facet_counts; prints PASS.
- directive candidate: `perspective_admin_api.py` -- FastAPI CRUD router /perspectives:
  create/update/delete restricted to admin (rbac_enforcer.require_role('admin') pattern), get/list
  open to org members; every mutation writes a product_audit_log-style row via write_service.
  ACCEPTANCE: __main__ asserts a member create is rejected (403 path) and an admin create + list
  round-trips the saved facet_filters; prints PASS.
- directive candidate: `perspective_diff_service.py` -- the trust-diff attach point:
  snapshot_perspective(id) persists the perspective's current membership {server_id, risk_tier}
  via write_service into `perspective_snapshots`; diff_perspective(id) compares live membership vs
  the last snapshot -> {entered, left, tier_changed:[{server_id, old, new}]} and queues an in-app
  notification row per change for the org's watchers (verdict_watchlist_service pattern; NO
  external connectors). ACCEPTANCE: __main__ snapshots a sample membership, simulates one tier
  change + one departure, asserts diff returns exactly those two and a notification row is queued
  for each; prints PASS.

**Presentation tier:**
- directive candidate: `perspective_tree_view.html` -- self-contained facet-tree navigator:
  left-rail facet tree with live counts (fetches facet_enum_service via its API mount), saved
  perspectives picker, results table with risk tier + per-axis chips (fetches
  /perspectives/{id}/servers), drill-down updates counts; no CDN, no localStorage, a11y labels;
  links each row to entity_detail_view.html.

## Appendix G - v2 "Ask MCPLookup" RAG search (directive candidates, NOT YET BUILT)

*Added 2026-07-02 -- chairman decision 2026-07-02: OPENED ALONGSIDE Appendix F (overriding the
6/27 "hold RAG until Perspectives holds" default; the one-bounded-anchor rule is superseded for
this pair, but each candidate below stays individually bounded). RAG here = free-text search over
the SCORED corpus with mandatory provenance -- retrieval is deterministic/lexical in v1 (stdlib
tokenization; NO embedding service, NO new external dependency); LLM synthesis is OPTIONAL, routed
via ladder_shim:8796 and STRICTLY flag-gated (ASK_LLM=1, default OFF => zero per-query LLM cost by
default). Answers may cite ONLY retrieved rows; below-threshold retrieval returns INSUFFICIENT
rather than a guess. Sources are ONLY mcp_server_registry + mcp_llm_axis_scores via write_service
/query -- never invent CSVs.*

- directive candidate: `ask_corpus_indexer.py` -- corpus builder: for each scored server compose a
  normalized snippet (name + description head + verdict + risk_tier + the 7 axis labels) and a
  stdlib-tokenized term list; upsert rows {server_id, snippet, terms (JSON), indexed_at} into
  `ask_corpus_index` via write_service /write; bounded batches with a resumable watermark row so
  re-runs are idempotent (no duplicate rows). ACCEPTANCE: __main__ on sample registry+axis rows
  asserts the snippet contains the verdict and an axis label, and a second run produces no new
  rows for unchanged input; prints PASS.
- directive candidate: `ask_retrieval_service.py` -- deterministic lexical retrieval:
  score ask_corpus_index rows against a query by term overlap + field weighting (name > verdict >
  axis labels > description), stdlib only, bounded LIMIT read via write_service /query; returns
  top-k [{server_id, score, provenance:{matched_fields, matched_terms}}]. ACCEPTANCE: __main__ on
  a 3-doc in-memory corpus asserts the query "weak auth github server" ranks the seeded
  auth_strength=WEAK github-source doc first and its provenance names the matched fields; prints
  PASS.
- directive candidate: `ask_answer_api.py` -- FastAPI POST /ask {query}: run ask_retrieval_service,
  synthesize the answer FROM RETRIEVED ROWS ONLY -- v1 synthesis is deterministic templating
  (per-server line: name, tier, the axis labels that matched); optional LLM polish ONLY when env
  ASK_LLM=1 via ladder_shim:8796 (default OFF, no call); response always carries
  citations:[{server_id, matched_fields}] and returns status INSUFFICIENT (not an answer) when the
  top retrieval score is below threshold or the corpus is empty. ACCEPTANCE: __main__ asserts (1)
  with ASK_LLM unset no network call is attempted, (2) every server named in the answer text
  appears in citations, (3) an empty corpus yields INSUFFICIENT; prints PASS.
- directive candidate: `ask_search_view.html` -- self-contained search page: query box, answer
  panel, citation chips linking entity_detail_view.html, visible INSUFFICIENT state, provenance
  ("why this result") expander per citation; no CDN, no localStorage, a11y.

## Appendix H - Council Roadmap 2026-07-02 (FATHER ruling; phased directive candidates)

*Council of 3 + FATHER convened 2026-07-02 on the post-v1.1/v2 roadmap. RULING: "Verification
catches up to shipping in week 1, distribution ships on fresh data in weeks 2-6." ALL SIX of
CONTRA's integrity gates adopted: (1) last_scored_at + STALE badge on every surface, (2) scheduled
perspective-snapshot cadence, (3) corpus auto-reindex on >5% registry drift, (4) >=5 curated
perspectives before promotion, (5) golden-sample verdict-drift CI gate, (6) freshness metadata on
all public surfaces. THE LINE: no signature, public API key, or agent-facing endpoint ships
against data older than its declared freshness SLA. Items below are the FACTORY lanes (bounded,
schema-grounded, exemplar-referencing); agent-owned work (rescore runs, CI wiring, auth-spine
design, announce) is NOT listed as candidates. Rejected outright: red-team/model A/B this cycle;
webhooks with external credentials (credential-segmentation decision stays parked until the
Phase-3 design decision).*

**PHASE 1 lanes (integrity; build first):**
- directive candidate: `freshness_metadata_api.py` -- FastAPI GET /api/freshness: per-server
  {last_scored_at, age_days, stale (age > SLA_DAYS=7)} from mcp_llm_axis_scores.scored_at joined
  to mcp_server_registry.last_assessed, plus a corpus-level summary {oldest, newest, stale_count};
  read via the app DB session (verdict_breakdown_api exemplar). ACCEPTANCE: __main__ on sample
  rows asserts a 10-day-old score reports stale=true and the summary stale_count matches; prints
  PASS.
- SUPERSEDED (CofC 2026-07-08, docs/DECISION_CADENCE_WRITE_PATH_2026_07_08.md): built as cadence_admin_api.py bulk endpoint, NOT a daemon. Do not re-propose. Was: `perspective_snapshot_daemon.py` -- the gate-2 cadence job: every
  SNAPSHOT_INTERVAL_HOURS (default 24) call perspective_diff_service.snapshot_perspective for
  every saved perspective, then diff_perspective to queue PerspectiveEvent rows; single-instance
  guard; heartbeat log line per cycle. Runs as a container daemon (daemon_wrapper pattern).
  ACCEPTANCE: __main__ with a fake session asserts one cycle snapshots every perspective exactly
  once and is idempotent within the interval; prints PASS.
- SUPERSEDED (CofC 2026-07-08, same ruling): built as cadence_admin_api.py drift-check endpoint, NOT a daemon. Do not re-propose. Was: `ask_corpus_drift_guard.py` -- the gate-3 trigger: compare
  count(ask_corpus_index) vs count(mcp_server_registry) and max(indexed_at) vs
  max(last_assessed); when drift > DRIFT_PCT (default 5) or scores are newer than the index,
  invoke ask_corpus_indexer.reindex and record a mesh-style audit row. ACCEPTANCE: __main__ on
  sample rows asserts drift 6% triggers and 4% does not; prints PASS.

**PHASE 2 lanes (differentiators on fresh data; blocked by gates 1-3,5-6):**
- directive candidate: `axis_evidence_api.py` -- explainable verdicts: GET
  /api/servers/{id}/evidence returns, per axis, {label, p_top, probs (the full distribution),
  model_version, scored_at, rule_overrides applied (trust_gating_override reason when present)}
  -- the "verify this, don't trust us" surface (verdict_breakdown_api exemplar; NO invented
  evidence text, only real stored fields). ACCEPTANCE: __main__ asserts the override reason
  surfaces for a trusted-capped sample and probs sum to ~1.0; prints PASS.
- directive candidate: `ask_query_expansion_v2.py` -- retrieval upgrade: extend
  ask_retrieval_service's fixed synonym map with axis-value expansions derived FROM THE LIVE
  ENUMS (facet_enum_service), bigram matching for server names, and recency tiebreak on
  indexed_at; pure functions, same provenance contract. ACCEPTANCE: __main__ asserts a
  two-word name query outranks single-term matches and provenance still lists matched fields;
  prints PASS.

**PHASE 3 lanes (distribution capstones; blocked by the Phase-3 auth design decision + all gates):**
- directive candidate: `perspective_email_digest.py` -- A-narrow: render unseen
  PerspectiveEvent rows per perspective into a daily digest (subject, plaintext + simple HTML
  body) and mark seen; delivery seam is an injected send callable (NO external credentials in
  this module -- the parked decision stays parked). ACCEPTANCE: __main__ renders a digest for 3
  sample events, asserts all three server_ids appear and events flip to seen; prints PASS.
- directive candidate: `scorecard_badge_api.py` -- GET /badge/{server_id}.svg: self-contained
  SVG shield (tier-colored) with the published tier + last_scored_at age; refuses (410 + grey
  "STALE" shield) when older than the freshness SLA -- the LINE enforced in code. ACCEPTANCE:
  __main__ asserts fresh sample renders tier color, stale sample renders the grey STALE shield;
  prints PASS.

**PHASE 4 lanes (chairman spec extension 2026-07-15: CVE surfacing follow-through + orphan-value wiring + ops honesty. Context: the SPA CVE panel shipped in #1481; these lanes make vuln intel DISCOVERABLE, keep provenance auditable, and mount already-built value. Same wiring rules as everywhere: DB access ONLY via write_service :8772 /query + /write; provenance-first; fail-visible; deterministic joins only.):**

- directive candidate: `server_cve_search_api.py` -- CVE discoverability: GET /vuln/servers_with_cves?severity=&limit=&offset= returning servers having >=1 vuln_link, joined to the registry: {server_id, name, url, risk_tier, cve_count, max_severity, latest_published_at}. SQL via requests.post('http://127.0.0.1:8772/query', ...) joining vuln_links -> vuln_advisories -> mcp_server_registry, GROUP BY server, ORDER BY cve_count DESC. Severity order CRITICAL>HIGH>MODERATE>MEDIUM>LOW>UNKNOWN. Exemplars: vuln_links_query_api.py, vuln_exposure_rollup_api.py. ACCEPTANCE: __main__ queries live, asserts every returned row has cve_count >= 1 and rows are ordered by cve_count desc; prints PASS.

- directive candidate: `cve_severity_rollup_api.py` -- fleet exposure matrix: GET /vuln/severity_rollup returns counts of DISTINCT linked servers and advisories cross-tabbed severity x risk_tier from vuln_links x vuln_advisories x mcp_server_registry, plus totals and generated_at (UTC ISO). No estimates: only counted rows. Exemplar: vuln_exposure_rollup_api.py. ACCEPTANCE: __main__ asserts totals equal the sum of cells and the distinct-server total <= count(distinct server_id) in vuln_links; prints PASS.

- directive candidate: `cve_facet_compile_service.py` -- deterministic facet membership compiler: servers_with_known_cve() -> set[str] and servers_with_curated_threat_ref() -> set[str] reading vuln_links and threat_intel_refs (is_aggregator=False only) via :8772/query; plus compile_filter(filters: dict, ids: set) applying a has_known_cve true/false intersection. Pure read-only. Exemplar: vuln_facet_extension.py (same membership semantics; this gives the perspectives compile path a deterministic set to intersect). ACCEPTANCE: __main__ asserts every id from servers_with_known_cve() has >=1 vuln_link row and the false-filter returns the complement within a sample; prints PASS.

- directive candidate: `vuln_link_provenance_audit.py` -- THE LINE for vuln claims: audit every vuln_links row for (a) advisory_id present in vuln_advisories, (b) match_confidence in [0,1], (c) match_basis in ('package_exact','repo_exact','name_version_exact','package_alias'), (d) the advisory has a non-empty source_url. Emits {checked, ok, violations:[{id, reason}]} and writes ONE summary row to audit_log via :8772/write (action='vuln_link_provenance_audit', meta=summary). ACCEPTANCE: __main__ runs live, asserts checked == count(vuln_links); prints PASS.

- directive candidate: `advisory_freshness_gate_probe.py` -- fail-visible feed freshness: for each feed in ('osv','ghsa','nvd') compute max(fetched_at) from vuln_advisories and age vs SLA_DAYS=7 (env ZO_VULN_FEED_SLA_DAYS); output {feed, newest, age_days, status fresh|STALE|EMPTY}; nonzero exit when any feed is STALE/EMPTY so cron surfaces it. NO default-fresh: unknown => STALE (an uncalled gate is not a gate). Exemplar: freshness_coverage_api.py. ACCEPTANCE: __main__ feeds a 30-day-old timestamp through the age check and asserts STALE, then live-runs and prints per-feed ages; prints PASS.

- directive candidate: `orphan_router_wiring_report.py` -- the 282-orphans map: scan the repo root for modules defining APIRouter(, parse app/main.py's ROUTER_MODULES mount list, and report {module, mounted: bool, tables_touched: [...]} ranked with unmounted modules touching high-value tables (vuln_*, threat_intel_refs, mcp_llm_axis_scores, mcp_score_disputes) first. Pure static TEXT scan (never import scanned modules), stdlib only. Output JSON to stdout AND /home/workspace/shared/outputs/orphan_router_report.json. ACCEPTANCE: __main__ asserts vuln_exposure_api reports mounted=True and at least one unmounted module is found, prints the top 10; prints PASS.

- directive candidate: `wire_high_value_routers_into_main.py` -- one-shot idempotent wiring script (NEW name; the earlier wire_orphan_value_routers directive ghosted with zero diff): read orphan_router_report.json (or compute inline), take the top N=5 unmounted router modules, and extend the ROUTER_MODULES list in app/main.py by an exact-anchor insert (write a .bak first; never regex-rewrite unrelated lines). Refuses (exit 2, clear message) when the anchor is not found EXACTLY once. Skips modules already present (idempotent). ACCEPTANCE: __main__ --dry-run prints the modules it WOULD mount and asserts the anchor is found exactly once; prints PASS.

- directive candidate: `dispute_backlog_summary_api.py` -- GET /disputes/backlog_summary: mcp_score_disputes grouped by status with oldest-pending age_days, counts by reason_category, and the last resolved_at -- surfaces a stuck review queue instead of hiding it. Exemplar: dashboard_summary_api.py. ACCEPTANCE: __main__ asserts group counts sum to count(*) of mcp_score_disputes; prints PASS.

- directive candidate: `registry_source_freshness_report.py` -- per registry_source honesty table: count, scored (>=1 mcp_llm_axis_scores row), never_scored, stale_beyond_sla (newest scored_at older than 7d), newest_scored_at. Output markdown + JSON to stdout. Exemplar: freshness_coverage_api.py. ACCEPTANCE: __main__ asserts scored + never_scored == count for a sampled source; prints PASS.

- directive candidate: `perspective_event_rollup_api.py` -- GET /perspectives/events_rollup: unseen PerspectiveEvent counts per perspective_id with newest created_at and a change_type breakdown -- the digest precursor surface (perspective_email_digest consumes it). Reads perspective_events + perspectives via :8772/query. ACCEPTANCE: __main__ asserts unseen counts are non-negative and every returned perspective_id exists in perspectives; prints PASS.

- directive candidate: `cadence_job_health_api.py` -- GET /cadence/health_rollup from cadence_job_runs: per job, last status, last finished_at, overdue bool vs CADENCE_SLA_HOURS=36 (never-ran => overdue=True, honest fail-closed). Exemplar: freshness_coverage_dashboard_api.py. ACCEPTANCE: __main__ asserts a never-ran fake job evaluates overdue=True; prints PASS.

**PHASE 5 lanes (chairman spec extension 2026-07-16: PLAN_200K instrumentation + lane-D audit gate + PHASE-4 facet follow-through. Context: PLAN_200K (#1492) commits 80.5K -> 200K assessed by 2026-10-15 via lanes A (directories) -> B (GitHub direct) -> C (npm/PyPI) -> D (gated tail, CLOSED until a 90% precision audit passes). These lanes give the scale-up HONEST instrumentation -- flat counts must be visible, not discovered -- pre-build the lane-D gate artifact, satisfy the binding rescore wall-clock condition, and finish the has_known_cve compile path opened in PHASE 4. Same wiring rules as everywhere: DB access ONLY via write_service :8772 /query + /write; deterministic; fail-visible; no estimates, no fabricated rates.):**

- directive candidate: `registry_growth_snapshot_rollup.py` -- daily counts spine for the 200K ramp: upsert one row per UTC day {snap_date, registry_rows, scored_servers, never_scored, per_source (JSON counts by registry_source)} into `registry_growth_snapshots` via :8772/write; idempotent (re-running the same day updates, never duplicates). Counts read via :8772/query from mcp_server_registry and mcp_llm_axis_scores. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ runs the upsert twice on sample counts, asserts the second run produces no duplicate row and per_source values sum to registry_rows; prints PASS.

- directive candidate: `registry_growth_progress_api.py` -- FastAPI GET /registry/growth_progress: {registry_rows, scored_servers, never_scored, assessed_pct} from mcp_server_registry + mcp_llm_axis_scores via :8772/query, plus PLAN_200K milestone context (target=200000, deadline 2026-10-15): remaining, days_remaining, required_daily_rate, and the observed 7-day daily delta computed from registry_growth_snapshots -- an empty or single-row snapshot table returns delta_unknown=true rather than a fabricated rate. Exemplar: dashboard_summary_api.py. ACCEPTANCE: __main__ asserts assessed_pct equals scored/target within rounding and that an empty snapshot table yields delta_unknown=true; prints PASS.

- directive candidate: `registry_source_lane_report.py` -- PLAN_200K lane honesty table: map registry_source values to lanes (A=directory aggregators, B=github_direct, C=npm/pypi package registries, UNMAPPED=everything else -- never silently bucket) and report per lane {sources, count, scored, never_scored, newest_created_at}, plus a FLAT flag when a lane count equals its value in the previous registry_growth_snapshots row (flat counts = check pipeline first). Markdown + JSON to stdout. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ asserts lane counts including UNMAPPED sum to the registry total and a same-count sample sets FLAT=true; prints PASS.

- directive candidate: `scoring_precision_audit_report.py` -- the lane-D gate artifact: deterministic seeded stratified sample (N per risk_tier, latest model_version only) from mcp_llm_axis_scores joined to mcp_server_registry via :8772/query, emitting an audit worksheet (JSON + markdown checklist) of {server_id, name, url, risk_tier, verdict, axis labels} with BLANK human-verdict columns, plus a summarize mode that computes precision from a filled worksheet and states PASS/FAIL vs the 0.90 bar. No self-grading: model labels are NEVER used as ground truth. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ asserts the sample is reproducible for a fixed seed and summarize on a synthetic filled worksheet computes the expected precision; prints PASS.

- directive candidate: `rescore_wallclock_projection_report.py` -- the PLAN_200K binding condition ("verify rescore wall-clock at 100K first"): compute observed servers/hour for the latest completed rescore window from cadence_job_runs and mcp_llm_axis_scores scored_at density via :8772/query, then project wall-clock hours at corpus sizes 100000 and 200000 and flag EXCEEDS_WINDOW when the projection breaks the job window (env ZO_RESCORE_WINDOW_HOURS, default 24). No completed run rows => status UNKNOWN, never a made-up projection. Exemplar: freshness_coverage_api.py. ACCEPTANCE: __main__ on a synthetic run of 1000 servers in 2h asserts the 200000 projection of 400h flags EXCEEDS_WINDOW; prints PASS.

- directive candidate: `never_scored_backlog_rollup.py` -- prioritization surface for the weekly never-scored-first rescore: rollup of the never-scored population (registry rows with zero mcp_llm_axis_scores rows) by registry_source x created_at age band {<7d, 7-30d, >30d} with totals, read via :8772/query only. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ asserts band totals sum to the never-scored total; prints PASS.

- directive candidate: `wire_has_known_cve_facet_into_compile.py` -- PHASE 4 follow-through (cve_facet_compile_service exists; the perspectives compile path never calls it): one-shot idempotent wiring script that inserts the has_known_cve intersection from cve_facet_compile_service into perspective_query_api's filter-compile step via an exact-anchor insert (write a .bak first; refuse with exit 2 and a clear message when the anchor is not found EXACTLY once; skip cleanly when already wired). Same discipline as wire_high_value_routers_into_main.py (exemplar). ACCEPTANCE: __main__ --dry-run asserts the anchor is found exactly once and prints the exact insertion it WOULD make; prints PASS.


**PHASE 6 lanes (chairman spec extension 2026-07-17: sprint-200K pipeline verification + cost/throughput honesty. Context: the 20260717-022858 score run completed 171,050 preds then LOST them at the results push (SCORE_FAIL, est $2.72); the registry jumped to 232K rows while scored held at 66.5K. This phase instruments the score-transfer pipeline end to end so a silent loss like that is surfaced by CODE, not by a chairman reading onstart.log. All read paths via :8772/query; no fabricated rates -- unknown is a valid answer.)**

- directive candidate: `score_results_push_verifier.py` -- verify a score-results branch is COMPLETE before import: given a directory of collected results (preds.jsonl.gz or preds.jsonl.gz.part.* chunks plus preds.sha256 manifest), recompute sha256 per part and for the reassembled whole, and report {parts_found, parts_expected, missing, corrupt, whole_sha_ok, line_count} as JSON + markdown. A missing manifest => status UNVERIFIABLE, never a silent pass. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ builds a synthetic 3-part file with a manifest, corrupts one part, and asserts the report flags exactly that part; prints PASS.

- directive candidate: `score_import_reconciliation_report.py` -- post-import honesty check for a rescore run: given the run's exported server_id list and mode (new|refresh), count how many landed in mcp_llm_axis_scores with scored_at >= fired_at via :8772/query and report {exported, landed, missing, dup_rows, pct_landed} with FAIL when pct_landed < 99. Missing run metadata => status UNKNOWN. Exemplar: freshness_coverage_api.py. ACCEPTANCE: __main__ on a synthetic export of 100 ids with 97 landed asserts missing=3 and FAIL=true; prints PASS.

- directive candidate: `never_scored_burndown_api.py` -- FastAPI GET /scoring/burndown: time series of never_scored from registry_growth_snapshots plus current value via :8772/query, with PLAN_200K context {target=200000, deadline 2026-10-15, required_daily_scoring_rate} computed from the CURRENT never_scored and days remaining; fewer than 2 snapshot rows => trend_unknown=true rather than a fabricated slope. Exemplar: registry_growth_progress_api.py. ACCEPTANCE: __main__ asserts required_daily_scoring_rate = never_scored/days_remaining within rounding and single-row snapshots yield trend_unknown=true; prints PASS.

- directive candidate: `registry_family_dedup_report.py` -- duplication honesty table: group mcp_server_registry by family key (normalized repo owner/name when url is a GitHub repo, else normalized package name, else the url itself -- state the rule in the output) and report {families, sids, dup_overhead_pct} overall and per registry_source, flagging any source above 40 pct overhead (fleet baseline 24.7 pct on 2026-07-16). Read-only via :8772/query. Exemplar: registry_source_lane_report.py. ACCEPTANCE: __main__ on a synthetic registry of 10 rows across 7 families asserts dup_overhead_pct=30 and the per-source flag fires on a crafted 50 pct source; prints PASS.

- directive candidate: `scoring_wave_cost_ledger_api.py` -- FastAPI GET /scoring/cost_ledger: read a weekly-rescore ledger.jsonl-shaped file (path via env ZO_RESCORE_LEDGER, default data/rescore_ledger.jsonl) and report per run {run_id, result, est_cost, phases_reached} plus {cumulative_cost, failed_run_cost, cost_per_1k_scored} and a CEILING_NEAR flag when cumulative cost in the current calendar week exceeds env ZO_RESCORE_WEEKLY_CEILING (default 10). Missing ledger => empty report, never invented rows. Exemplar: dashboard_summary_api.py. ACCEPTANCE: __main__ on a synthetic ledger of one $2.72 fail and one $0.33 pass asserts failed_run_cost=2.72 and cumulative=3.05; prints PASS.

- directive candidate: `harvest_lane_throughput_report.py` -- sprint throughput table: per lane (A directories, B github_direct, C npm/pypi, UNMAPPED) compute rows added per day for the last 7 days from mcp_server_registry created_at via :8772/query, plus scored-per-day from mcp_llm_axis_scores scored_at, and a STALLED flag for any lane with additions yesterday but zero today. Markdown + JSON to stdout. Exemplar: registry_source_lane_report.py. ACCEPTANCE: __main__ on synthetic rows asserts per-lane daily sums match and STALLED fires on a crafted lane; prints PASS.

- directive candidate: `sprint_progress_dashboard_api.py` -- FastAPI GET /sprint/progress: single sprint-tracker surface joining {registry_rows, scored_servers, never_scored} live counts via :8772/query with distinct-family count and the sprint target context (env ZO_SPRINT_TARGET default 200000, env ZO_SPRINT_DEADLINE default 2026-07-19), reporting {assessed_sids, families, pct_to_target, on_track} where on_track requires the observed 24h scoring delta to meet the required remaining daily rate -- no 24h delta computable => on_track=null. Exemplar: registry_growth_progress_api.py. ACCEPTANCE: __main__ asserts pct_to_target math and that an uncomputable delta yields on_track=null; prints PASS.

- directive candidate: `cve_facet_compile_wiring_v2.py` -- RETRY under a NEW name (wire_has_known_cve_facet_into_compile burned its sentinel on two hollow builds closed 2026-07-17; stale done-sentinels demand fresh names): one-shot idempotent wiring script inserting the has_known_cve intersection from cve_facet_compile_service into perspective_query_api's filter-compile step via an exact-anchor insert. MUST read the REAL perspective_query_api.py and cve_facet_compile_service.py from disk (reads list them); a build that invents its own models instead of importing the real modules is WRONG. Write a .bak first; exit 2 with a clear message when the anchor is not found exactly once; skip cleanly when already wired. Exemplar: wire_high_value_routers_into_main.py. ACCEPTANCE: __main__ --dry-run asserts the anchor is found exactly once in the real file and prints the exact insertion it WOULD make; prints PASS.

**PHASE 7 lanes (chairman spec extension 2026-07-18: post-wave honesty + ceiling observability. Context: ScoreWave run 20260717-182921 landed +105,685 rows overnight (scored 66.5K -> 172,250; M1 a month early), and the same day exposed three scars that only a human noticed: the ask-corpus drift job had been hard-failing its 200K cost ceiling silently for 2 days; a healthy import was nearly killed because under write load it shows 0 CPU, a silent buffered log, and a timing-out /freshness; and the gaps map exhausted at 11:52Z leaving the builder idle until a chairman read /proc/<pid>/fd/1. This phase turns each of those into CODE. All read paths via :8772/query; no fabricated rates -- unknown is a valid answer.)**

- directive candidate: `cadence_job_sla_report.py` -- cadence SLA honesty table: per cadence job (from cadence_job_runs via :8772/query) report {job, last_ok_at, hours_since_ok, sla_hours, overdue} with sla_hours from env (ZO_CADENCE_SLA_DRIFT default 36, ZO_CADENCE_SLA_SNAPSHOTS default 36) and a top-level FAIL when any job is overdue. A job with zero ok runs ever => status NEVER_SUCCEEDED, never a silent pass. Markdown + JSON to stdout. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ on synthetic runs where one job's last ok is 40h old against a 36h SLA asserts overdue=true for exactly that job and FAIL=true; prints PASS.

- directive candidate: `cost_ceiling_headroom_report.py` -- ceiling observability: enumerate the fleet's hard cost ceilings (CADENCE_REINDEX_MAX_ROWS vs live registry_rows count via :8772/query; CADENCE_MAX_PERSPECTIVES vs live perspective count; ZO_RESCORE_WEEKLY_CEILING vs current-week ledger spend from data/rescore_ledger.jsonl) and report per ceiling {name, ceiling, current, pct_used, state} where state is OK / WARN (>=80 pct) / TRIPPED (>=100 pct). A ceiling whose current value cannot be observed => state UNKNOWN, never OK. Exemplar: dashboard_summary_api.py. ACCEPTANCE: __main__ asserts 232174 against a 200000 ceiling yields TRIPPED, 150000 yields OK, and 165000 yields WARN; prints PASS.

- directive candidate: `import_liveness_probe.py` -- encode the 20260717-182921 SCAR: an import under write load looks dead from outside (0 CPU, buffered log, /freshness timing out) while actually landing rows. Probe takes two samples N seconds apart (env ZO_PROBE_INTERVAL default 60) of the scored-rows count via :8772/query plus the run state.json mtime (path via env ZO_RUN_STATE), and emits VERDICT in {IMPORT_ALIVE (rows grew), STALLED (no growth across BOTH samples and state.json stale), UNKNOWN (single sample or unreadable state)}. It must NEVER report STALLED from one sample. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ on synthetic samples with growing counts asserts IMPORT_ALIVE and a single-sample run asserts UNKNOWN; prints PASS.

- directive candidate: `family_coverage_progress_api.py` -- FastAPI GET /sprint/family_coverage: the honest sprint metric is FAMILIES, not sids (fleet dup overhead 24.7 pct on 2026-07-16). Via :8772/query compute {families_total, families_scored, pct_families, sids_scored} using the family key rule from registry_family_dedup_report.py (normalized GitHub owner/name, else normalized package name, else url -- import the real module, do not re-invent the rule). Fewer than 1 family => empty report, never a fabricated pct. Exemplar: registry_growth_progress_api.py. ACCEPTANCE: __main__ on a synthetic registry of 10 rows across 7 families with 5 families scored asserts pct_families within rounding of 71.4; prints PASS.

- directive candidate: `family_first_wave_planner.py` -- plan the next score wave to maximize NEW family coverage: select at most one unscored sid per family that has NO scored member (prefer the sid whose url is a GitHub repo), and emit a run-manifest JSON {sids, families_covered, est_cost} with est_cost from env ZO_SCORE_COST_PER_1K (default 0.02 USD). Read-only via :8772/query; the planner emits a manifest and never launches or rents anything. Exemplar: registry_source_lane_report.py. ACCEPTANCE: __main__ on a synthetic registry of 3 families where one family already has a scored member asserts the manifest picks exactly 2 sids from the 2 uncovered families; prints PASS.

- directive candidate: `wave_import_axis_drift_report.py` -- post-import sanity at scale: for rows scored in a given window (env ZO_WAVE_SINCE, ISO timestamp) compare each axis's label-share distribution against the pre-window corpus via :8772/query, reporting per axis {max_class_share_delta, degenerate} where degenerate=true when the wave puts >90 pct of mass on one class and the prior corpus did not. A +105K import that scored everything the same would silently poison the moat -- this report is the tripwire. Fewer than 100 wave rows => status INSUFFICIENT_SAMPLE. Exemplar: scoring_axis_label_distribution_api.py. ACCEPTANCE: __main__ on two synthetic distributions, one crafted degenerate, asserts the flag fires only on the degenerate axis; prints PASS.

- directive candidate: `wedge_spend_ledger_report.py` -- wasted-spend truth: from the vast ledger (env ZO_VAST_LEDGER default data/vast_ledger.jsonl) and the wedged-machine blocklist (env ZO_WEDGED_HOSTS default data/wedged_hosts.json) report per calendar week {wedge_events, wasted_usd, blocklisted_hosts, top_offender_host}. 2026-07-17 burned ~$0.90 on two wedged hosts -- that number should come from CODE, not a chairman's recollection. Missing files => empty report, never invented rows. Exemplar: scoring_wave_cost_ledger_api.py. ACCEPTANCE: __main__ on a synthetic ledger with two wedge rows of 0.55 and 0.35 asserts wasted_usd=0.90 and wedge_events=2; prints PASS.

- directive candidate: `directive_queue_health_api.py` -- FastAPI GET /factory/queue_health: read the directive dirs (env ZO_DIRECTIVES_DIR default directives/) and report {proposed, pending, done_24h, newest_done_age_min, starved} counting ONLY live *.json (exclude .bak*, .duplicate, retired markers -- today 12 stale .bak files in pending/ masked a fully starved queue), where starved = (proposed + pending == 0). Today's starvation floor was discovered by a human reading a deleted-inode log; starvation becomes an API. Exemplar: dashboard_summary_api.py. ACCEPTANCE: __main__ on a synthetic tmp dir with 2 live pending JSONs plus 3 .bak files asserts pending=2 and starved=false, then on emptied dirs asserts starved=true; prints PASS.


**PHASE 8 lanes (chairman spec extension 2026-07-19: scale honesty at 232K + the new data surfaces. Context: the fleet crossed 232,180 registry rows with canonical_family materialized on every row (162,832 families, PR #1621) and the FIRST score-change delta dataset landed via score_change_events (PR #1619, ScoreWave2 run 20260719-003024: +45 new servers, 65K refresh, $1.19, instance destroyed clean). Same day, the 232K scale broke things quietly: the Fly-side ask-corpus reindex was OOM-killed at ~790MB leaving cadence runs 24/26 as zombie 'running' rows (and killed the co-resident snapshots worker), the snapshots job ran 3x slower than its 172K baseline, and a chairman had to hand-compute whether the 65K refresh actually stamped rows. This phase turns each of those into CODE, and finally gives the architect's own convergence a report. All read paths via :8772/query; no fabricated rates -- unknown is a valid answer.)**

- directive candidate: `cadence_runtime_trend_report.py` -- cadence slowdown honesty: per cadence job (cadence_job_runs via :8772/query, status='ok') report {job, runs_30d, median_minutes, last_minutes, slowdown_x} where slowdown_x = last/median, flagging WARN when slowdown_x >= 2.0 (perspective snapshots went 8min -> 24.3min when the registry grew 172K -> 232K on 2026-07-19; that trend should be a report, not a chairman's recollection). Fewer than 3 ok runs for a job => status INSUFFICIENT_HISTORY, never a fabricated trend. Markdown + JSON to stdout. Exemplar: registry_source_freshness_report.py. ACCEPTANCE: __main__ on synthetic runs where one job's last duration is 3x its median asserts WARN fires for exactly that job; prints PASS.

- directive candidate: `canonical_family_drift_probe.py` -- idempotence tripwire for the materialized column: sample N rows (env ZO_FAMILY_PROBE_SAMPLE default 500) from mcp_server_registry via :8772/query and recompute the family key with the real rule from registry_family_dedup_report.py (import the module, do NOT re-invent the rule), reporting {sampled, matches, drift_rows, drift_pct} and VERDICT in {CLEAN (drift 0), DRIFTED (any mismatch), UNKNOWN (sample unavailable)}. The 2026-07-19 backfill proved rerun=0 writes / drift=0; this probe keeps that invariant true as harvesters write new rows. Exemplar: registry_family_dedup_report.py. ACCEPTANCE: __main__ on a synthetic registry where one row's stored canonical_family diverges from the rule asserts VERDICT=DRIFTED with drift_rows=1; prints PASS.

- directive candidate: `family_rollup_api.py` -- FastAPI GET /families/{family_key}: the first product surface that READS the canonical_family column (never recomputes the rule): via :8772/query return {family, members, sources, worst_risk_tier, verdicts: {verdict: count}, newest_last_assessed} across all registry rows sharing that canonical_family. Unknown family => 404, empty string => 422; a family of 1 is a valid rollup, not an error. Exemplar: server_risk_contributors_api.py. ACCEPTANCE: __main__ on a synthetic registry with one family of 3 rows across 2 sources (tiers LOW, HIGH, MEDIUM) asserts members=3, sources=2, worst_risk_tier=HIGH; prints PASS.

- directive candidate: `score_change_delta_report.py` -- the first consumer of the delta dataset: from score_change_events via :8772/query for a window (env ZO_DELTA_SINCE, ISO timestamp) report {events, servers_changed, by_axis: {axis: {changed, worsened, improved}}, top_movers: 10 servers by axis-change count} where worsened/improved compare new_label_index vs prev_label_index (higher index = worse; use the index columns, do not re-map labels). Zero events in window => status NO_CHANGES, an honest and valid answer. Exemplar: wave_import_axis_drift_report.py. ACCEPTANCE: __main__ on synthetic events where one server worsens on 2 axes and another improves on 1 asserts servers_changed=2 and worsened=2, improved=1; prints PASS.

- directive candidate: `score_change_timeline_api.py` -- FastAPI GET /servers/{server_id}/score_timeline: per-server change history from score_change_events via :8772/query ordered by event_ts, each entry {event_ts, axis_name, prev_label, new_label, direction} with direction from label_index deltas (worsened|improved|first_score when prev is null). A server with zero change events but existing scores => {"timeline": [], "note": "no changes recorded since delta capture began 2026-07-19"} -- never fabricate history that predates the capture. Exemplar: verdict_history_timeline_api.py. ACCEPTANCE: __main__ on synthetic events for one server (one worsening, one first_score) asserts timeline length 2 with correct directions; prints PASS.

- directive candidate: `wave_refresh_verification_report.py` -- refresh honesty (2026-07-19 scar: after a 65K refresh wave the chairman could not tell from /freshness whether refreshed rows were actually restamped): given a wave state.json (env ZO_RUN_STATE) with {new_servers, refresh_servers, fired_at}, verify via :8772/query that (a) scored-server count grew by ~new_servers and (b) at least refresh_servers*0.9 rows have scored_at/last_assessed newer than fired_at, reporting VERDICT in {VERIFIED, PARTIAL (some but <90 pct), NOT_STAMPED (new servers landed but refresh timestamps did not advance), UNKNOWN}. Exemplar: import_liveness_probe.py. ACCEPTANCE: __main__ on synthetic counts where refresh stamps did not advance asserts NOT_STAMPED, and on full advancement asserts VERIFIED; prints PASS.

- directive candidate: `import_row_delta_audit.py` -- post-import accounting as CODE (2026-07-19 the chairman hand-verified 1,205,750 + 45*7 = 1,206,065): given before/after freshness snapshots (env ZO_FRESHNESS_BEFORE / ZO_FRESHNESS_AFTER, JSON files) and the wave manifest {new_servers, refresh_servers, axes_per_server default 7}, assert scores_rows_delta == new_servers*axes and flag {OK, REFRESH_APPENDED (delta larger: refresh appended instead of updating in place -- row bloat), ROWS_MISSING (delta smaller), UNKNOWN}. Exemplar: cost_ceiling_headroom_report.py. ACCEPTANCE: __main__ with before=1205750, after=1206065, new=45, axes=7 asserts OK, and after=1660065 asserts REFRESH_APPENDED; prints PASS.

- directive candidate: `ladder_rung_convergence_report.py` -- the architect's own report card: parse the directive-generator log (env ZO_GEN_LOG default logs from sentinel_directive_generator_goose) counting per model rung the cycles, converged (proposed > 0), non_converged (the stable marker 'ARCHITECT NON-CONVERGENCE'), and rotation events, reporting {rung, cycles, converged_pct} plus VERDICT DEGENERATE when a rung has >= 10 cycles at 0 pct convergence (on 2026-07-19 the mistral rung burned every cycle emitting TOOL: calls as prose while the deterministic floor fed the builder -- that pattern should page a report, not require a chairman reading a deleted-inode log). Missing log => UNKNOWN. Exemplar: wedge_spend_ledger_report.py. ACCEPTANCE: __main__ on a synthetic log with one rung at 0/12 converged and another at 3/5 asserts DEGENERATE fires only for the first; prints PASS.

