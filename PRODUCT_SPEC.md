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


**PHASE 8b lanes (chairman spec extension 2026-07-19: FU-001 run-ledger lift from the spec-target queue. Context: fire_score.py / finalize_score.py and the ScoreWave + rescore harnesses launch paid vast runs without writing run-ledger entries, so the audit's run-reconciliation half is blind -- it cannot reconcile live instances/spend against intended runs. This lift lands the ledger as a real table plus a read-only reconciliation surface. Out of lane: the two-line POST hook inside fire_score.py / finalize_score.py stays an attended-session edit after the surface exists -- guardrails keep hand edits out of paid-launch scripts during unattended runs.)**

- directive candidate: `score_run_ledger_writer.py` -- run-ledger ingestion over the real schema: add a `score_run_ledger` table via a named alembic migration (revision slug `add_score_run_ledger`; columns run_id, score_branch, results_branch, vast_instance_id, dph, fired_at, imported_at, finalized_at, outcome), model declared in app.models and ALL access through app.db / app.models -- never an inline/mock DB -- plus a trust_gate-protected FastAPI POST /runs/ledger writer recording one row per fired run, so paid vast launches stop being invisible to the audit. Exemplar: verdict_breakdown_api.py (real app.db/app.models access, trust_gate, TestClient + dependency_overrides self-test). ACCEPTANCE: __main__ TestClient + dependency_overrides -> SQLite posts one ledger row and reads it back asserting run_id, vast_instance_id and dph round-trip exactly; prints PASS.

- directive candidate: `run_reconciliation_report.py` -- read-only FastAPI GET /runs/reconciliation (trust_gate) joining `score_run_ledger` against imported score rows in the real schema (mcp_llm_axis_scores run/wave stamps via app.db / app.models -- no mock DB), reporting {ledgered_runs, runs_with_imports, orphan_instances: ledger rows whose vast instance produced no imported score rows, unledgered_runs: imported runs with no matching ledger entry}. Zero rows on either side is a valid, honest answer -- never fabricate a match. Exemplar: verdict_breakdown_api.py. ACCEPTANCE: __main__ TestClient + dependency_overrides -> SQLite seeds one fully reconciled run, one orphan ledger instance and one unledgered imported run, asserts each lands in exactly one bucket; prints PASS.


**PHASE 9 lanes (chairman spec extension 2026-07-20: factory liveness + gate truth. Context: this phase is written off a single day's forensics, and every lane encodes a specific thing that went wrong silently. (1) The builder was DEAD for 61.6 hours -- goose_runner's last cycle on 2026-07-17T02:17Z, next on 2026-07-19T15:53Z, with zero log lines on 07-18 and a matching 3-day hole in both `build_provenance` and `mesh_events`. The watchdog restarts named daemons (it restarted threat_intel_ingestor on 07-20) but never restarted goose_runner, loop_watch has been disabled since 06-24, and THREE consecutive daily chairman reviews reported the factory healthy. Nothing in CODE asserts "a build happened recently". (2) Every one of the seven completion gates recorded the same hardcoded rejection string in the ledger, so 17 ghost rows on 07-20 all claimed a missing output_file when 12 were actually edit-class builds that changed nothing -- the direct signal for the 246 unmounted routers (PR #1669 fixes the write side; nothing yet READS it). (3) The directive queue reached exactly zero at 12:04Z with the builder idling on "No eligible directives found". All read paths via :8772/query; absence is a finding, not a blank -- a lane that cannot observe its input reports UNKNOWN, never OK.)**

- directive candidate: `factory_liveness_continuity_probe.py` -- the missing global invariant: assert that the factory is ALIVE, not merely that its last build looked fine. Via :8772/query read `build_provenance` and report {last_build_at, hours_since_last_build, builds_24h, builds_48h, distinct_days_with_builds_7d, longest_gap_hours_7d} plus VERDICT in {ALIVE (>=1 build in 24h), STALLED (0 builds in 24h but <48h), FACTORY_DEAD (0 builds in 48h), UNKNOWN (table unreadable -- never OK)}. Must also flag CONTINUITY_HOLE when any 7-day interior day has zero rows while both neighbours have rows: the 2026-07-17..19 outage was invisible precisely because every existing check was differential ("did the last build pass?") and none was a global liveness assertion. Read-only; stdlib + requests. Exemplar: `import_liveness_probe.py`. ACCEPTANCE: `__main__` on synthetic rows asserts (a) a 61.6h gap yields FACTORY_DEAD, (b) a 3h gap yields ALIVE, (c) a series with rows on day 1,2 and 4 but none on day 3 sets CONTINUITY_HOLE=true, and (d) an unreadable table yields UNKNOWN not OK; prints PASS.

- directive candidate: `build_gate_attribution_report.py` -- make the newly-attributed ledger legible (consumes the `gate=<key>: <reason>` prefix written by PR #1669). Via :8772/query over a window (env ZO_GATE_WINDOW_HOURS default 48) read `build_provenance` and report per gate key {gate, rejections, pct_of_failures, distinct_directives, example_directive} plus totals {builds, passed, failed, unattributed} and a DOMINANT_GATE finding when one gate accounts for >=40 pct of failures. Rows whose error carries no `gate=` prefix are counted as `unattributed` and reported as such -- pre-#1669 history must never be silently bucketed into a specific gate. Exemplar: `wedge_spend_ledger_report.py`. ACCEPTANCE: `__main__` on synthetic rows with 6 edit_diff, 2 selftest and 3 legacy unattributed failures asserts edit_diff is DOMINANT_GATE at 54.5 pct, unattributed=3, and that no legacy row is attributed to a named gate; prints PASS.

- directive candidate: `edit_class_directive_validator.py` -- the emission contract as a pure, importable validator (CofC 2026-07-20 ruling item 3). Given a directive dict, classify it and return {verdict: VALID|REJECT, reasons: [...], directive_class: create|edit|unknown}. REJECT when a directive is edit-class -- determined by the FACT that its named subject module already exists on disk, NOT by prose verbs, which the generator routes around (see the narration loophole, #1075) -- while declaring `output_file` null and `handler` "generate_file". A rejection must name the target file it expected and the required repair, so the caller can bounce it back as a repair prompt rather than dropping it (silent drops cause +0 starvation). Pure function over a dict plus a filesystem root; no network, stdlib only; it does NOT modify the generator -- wiring it in is a separate, attended decision. Exemplar: `schema_prm_guard.py`. ACCEPTANCE: `__main__` asserts (a) a directive naming an EXISTING module with output_file null and handler generate_file is REJECT with the target named in the reason, (b) the same directive naming a NON-existent module is VALID (a genuine create), (c) a directive with an explicit target_file and handler edit_file is VALID, and (d) a directive whose prose says "wire" but whose subject does not exist is VALID -- verbs are not evidence; prints PASS.

- directive candidate: `unmounted_router_census_report.py` -- the decision input for the 2026-07-21 mounts.toml design (the reachability ratchet landed OBSERVE-only in #1656 with a baseline of 246 unmounted routers of 277). Read the ratchet census artifact (env ZO_RATCHET_CENSUS default artifacts/reachability_ratchet.json; when absent, recompute by static TEXT scan -- never import scanned modules) and report per unmounted router {module, declared_prefix, tags, route_count, mount_idiom} where mount_idiom classifies how the module WOULD mount: `registry_uniform` (exactly one APIRouter symbol named `router`, mountable by the two-line app_router_registry pattern), `nonstandard` (multiple or differently-named router symbols), or `unknown`. Top-level output must include {total, unmounted, registry_uniform_pct} -- the number that decides whether a mechanical mount lane is defensible. Read-only, stdlib only. Exemplar: `orphan_router_wiring_report.py`. ACCEPTANCE: `__main__` on a synthetic tree with 3 modules (one single `router`, one with two APIRouter symbols, one with none) asserts registry_uniform=1, nonstandard=1, and that the module with no APIRouter is excluded from the census entirely; prints PASS.

- directive candidate: `ghost_retry_burn_report.py` -- what the retry loop actually costs. Via :8772/query group `build_provenance` by directive over a window (env ZO_BURN_WINDOW_HOURS default 72) and report per directive {directive, attempts, ever_passed, gates_hit: [...], retired} plus totals {directives, wasted_attempts (attempts on directives that never passed), pct_attempts_wasted}, ranked by wasted attempts. On 2026-07-20 three wire/integrate directives burned 3 attempts each and retired, which is 9 wasted builds that no report has ever named. A directive with a single passing attempt contributes zero waste. Exemplar: `wedge_spend_ledger_report.py`. ACCEPTANCE: `__main__` on synthetic rows with one directive at 3 attempts never passing and one at 1 attempt passing asserts wasted_attempts=3 and pct_attempts_wasted=75; prints PASS.

- directive candidate: `daemon_roster_coverage_report.py` -- close the gap the 61.6h outage exposed: the watchdog can only restart what it knows about. Parse the daemon roster (env ZO_GO_SH default go.sh) and the watchdog's supervised set (env ZO_WATCHDOG_CONF), compare both against the live process list supplied as an input list of process command strings (the report NEVER shells out or kills anything), and report per daemon {daemon, in_roster, watchdog_supervised, running} with findings UNSUPERVISED (running and in roster but not supervised -- goose_runner's exact 2026-07-17 state), ORPHAN (running but in neither), and MISSING (in roster, supervised, not running). Read-only and side-effect free by construction. Exemplar: `cadence_job_health_api.py`. ACCEPTANCE: `__main__` on a synthetic roster of 3 daemons where one runs unsupervised and one is absent asserts exactly one UNSUPERVISED and one MISSING, and that no subprocess is spawned; prints PASS.


**PHASE 10 lanes (chairman spec extension 2026-07-21: measured-not-asserted + the discrimination crisis. Context: the queue reached proposed=0 pending=1 with the starvation floor reporting "gaps map is EXHAUSTED", which is why this anchor exists. Every lane below encodes something this morning got WRONG about its own state rather than something that broke. (1) The generator logs a fixed string -- "did NOT reach propose_directive (tool-call loop / over-exploration)" -- on every +0 cycle, and today's transcripts falsify it: at 09:12:53Z and again at 11:50Z goose rendered `propose_directive zo_directive_bridge` with full parameters, so the call WAS reached and something after it failed. The bridge writes no log anywhere, so its accept/reject/error return is recorded on no durable surface and the most-read diagnostic line in the factory is a guess. (2) The runtime checkout drifted behind origin/main three times during a single review; the starvation floor even prints "the anchor may NOT be spent; this checkout may simply predate the refill" -- it knows it cannot tell, and nothing resolves the ambiguity. The deploy task's own description claims "every 3h" while its cron is daily. (3) Both directives built today passed goose tier1 and were BLOCKED by no_hollow, then rescued by the deterministic zo-ladder-high engine; the builder ladder's per-rung hollow rate is measured by nothing. (4) The reachability ratchet was armed today (baseline pinned 276, declare-or-mount hatch) -- the mount-lane design review on 2026-07-23 needs to know which orphans anything actually CALLS. (5) 99.47 pct of the scored corpus is HIGH or CRITICAL, with 54 LOW servers out of 172,295: the tier we sell discriminates almost nothing. All read paths via :8772/query; absence is a finding, not a blank -- a lane that cannot observe its input reports UNKNOWN, never OK.)**

- directive candidate: `propose_directive_outcome_log.py` -- stop asserting the cause of `+0`. A pure, importable reconciler that takes a captured goose transcript (string) plus the directive-dir state before/after a cycle, and returns {calls_rendered, calls_landed, verdict, evidence} where verdict is in {REACHED_AND_LANDED, REACHED_NOT_LANDED (the call is visible in the transcript but no new directive file appeared -- today's actual case, cause unknown and reported as unknown), NOT_REACHED (no call rendered), PROSE_ONLY (fenced ```json or `zo_directive_bridge__propose_directive(` python-call blocks emitted as narration with no real invocation -- count this SEPARATELY, both shapes appeared in the same transcript today), UNKNOWN}. It must count prose-shaped and genuinely-rendered calls as different things and NEVER collapse them, because #1635 salvages one and only the bridge can explain the other. Pure function over strings and a directory listing; stdlib only; it does NOT modify the generator -- rewiring the non-convergence message is a separate attended decision. Exemplar: `edit_class_directive_validator.py`. ACCEPTANCE: `__main__` asserts (a) a transcript with a rendered `propose_directive` and no new file yields REACHED_NOT_LANDED, (b) the same transcript with a matching new directive file yields REACHED_AND_LANDED, (c) a transcript containing only a fenced json block yields PROSE_ONLY and not NOT_REACHED, and (d) an empty transcript yields NOT_REACHED; prints PASS.

- directive candidate: `runtime_checkout_drift_probe.py` -- resolve the ambiguity the starvation floor admits it cannot. Compare the runtime checkout's HEAD against origin/main (both supplied as inputs -- the probe NEVER fetches, pulls, deploys or shells out to git remote) and report {head, origin_head, behind, drifted, spec_files_changed, anchor_may_be_stale} where anchor_may_be_stale is true when the checkout is behind AND any of the drifted paths matches a spec/anchor glob (env ZO_ANCHOR_GLOBS default "PRODUCT_SPEC.md,docs/ROADMAP*.md"). The starvation floor currently prints "gaps map is EXHAUSTED ... the anchor may NOT be spent; this checkout may simply predate the refill" -- an exhaustion claim and a staleness caveat with no way to choose between them. This lane makes the choice observable: a checkout behind main by a commit that touched the spec CANNOT honestly report exhaustion. Read-only, stdlib only. Exemplar: `factory_liveness_continuity_probe.py`. ACCEPTANCE: `__main__` asserts (a) equal heads yield drifted=false and anchor_may_be_stale=false, (b) a behind checkout whose drift touches PRODUCT_SPEC.md yields anchor_may_be_stale=true, (c) a behind checkout touching only unrelated modules yields drifted=true but anchor_may_be_stale=false, and (d) an unknown origin head yields UNKNOWN, never false; prints PASS.

- directive candidate: `builder_rung_hollow_rate_report.py` -- the builder ladder's report card, the sibling `ladder_rung_convergence_report.py` never got (that one grades the ARCHITECT). Parse the goose_runner log (env ZO_RUNNER_LOG) counting per rung the directives attempted, `[no-hollow] ... BLOCKED`, `[ghost-guard] ... not completing`, engine-fallback rescues (`[engine] <rung> wrote N bytes`), and selftest Tier-0 degradations, reporting {rung, attempts, hollow_blocked, rescued_by_engine, hollow_rate_pct} plus VERDICT RUNG_DEGENERATE when a rung has >= 5 attempts at >= 80 pct hollow_rate. On 2026-07-21 both attempted directives were blocked as hollow on zo-ladder-mistral and both were rescued by the deterministic zo-ladder-high engine -- a 100 pct tier1 hollow rate that no surface reports, and which matters because it decides whether the agentic rung is earning its latency at all. Missing log => UNKNOWN. Read-only, stdlib only. Exemplar: `ladder_rung_convergence_report.py`. ACCEPTANCE: `__main__` on a synthetic log with one rung at 5/5 hollow-blocked and another at 1/6 asserts RUNG_DEGENERATE fires only for the first, and that engine-rescue lines are counted as rescues rather than as passes for the failing rung; prints PASS.

- directive candidate: `orphan_router_caller_probe.py` -- the input the 2026-07-23 mount-lane review actually needs, and the direct encoding of the CVE UI-orphan lesson ("the SPA never called /vulns -- grep the SPA before calling a surface shipped"). Read the ratchet census (env ZO_RATCHET_CENSUS default artifacts/reachability_ratchet.json) and static-scan the frontend surface (env ZO_FRONTEND_GLOBS default "app/static/**/*.html,app/static/**/*.js,app/templates/**/*.html") for literal references to each orphan's declared route paths, reporting per orphan {module, declared_prefix, route_count, callers: [...], called} plus totals {orphans, called_by_frontend, uncalled, undecidable} where undecidable counts orphans declaring NO prefix (194 of 276 today -- their full path cannot be constructed, so their callability is genuinely unknown and must NEVER be reported as uncalled). The triage this feeds is three-way -- mount now / mount behind auth / DELETE -- and a module nothing calls and nothing can reach is a deletion candidate, not a mounting backlog item. Static TEXT scan only; never import a scanned module. Exemplar: `unmounted_router_census_report.py`. ACCEPTANCE: `__main__` on a synthetic census of 3 orphans (one whose route literal appears in a fixture HTML, one whose does not, one declaring no prefix) asserts called=1, uncalled=1, undecidable=1, and that the no-prefix module is NOT counted as uncalled; prints PASS.

- directive candidate: `risk_tier_threshold_calibration_probe.py` -- the cheapest test that separates the two live hypotheses behind the discrimination crisis (FU-058, P1: CRITICAL 65,269 / HIGH 106,118 / MEDIUM 854 / LOW 54 across 172,295 scored representatives -- 99.47 pct in the top two bands). Via :8772/query read the `overall_risk` axis LABEL distribution from mcp_llm_axis_scores and, separately, the tier distribution from mcp_server_registry, then report {axis_label_histogram, tier_histogram, tier_cut_points, mass_above_cut_pct, entropy_bits} plus VERDICT in {THRESHOLD_SUSPECT (the axis labels are spread but the tiers are not -- a flattening at the mapping layer, fixable with no model work), MODEL_SUSPECT (the axis labels are themselves concentrated >90 pct on one class -- mode collapse, which belongs with the SFT acceptance bar), BOTH, UNKNOWN}. It must read the axis mapping from the schema contract (`schemas/risk_axis_mapping_v1.json` shape) rather than inferring class enums from observed values -- `auth_strength` has 4 classes, not 6, and sample-derived enums have burned this before. This lane DIAGNOSES only: it changes no thresholds and rescoring nothing. Exemplar: `scoring_axis_label_distribution_api.py`. ACCEPTANCE: `__main__` asserts (a) a synthetic spread axis histogram with a collapsed tier histogram yields THRESHOLD_SUSPECT, (b) a collapsed axis histogram yields MODEL_SUSPECT, (c) both collapsed yields BOTH, and (d) an empty axis table yields UNKNOWN not THRESHOLD_SUSPECT; prints PASS.

- directive candidate: `axis_change_attribution_probe.py` -- make the delta summary answer the one question it exists to answer (FU-059: run 20260719-003024 reported `changed` 32,545 on SIX of seven axes on byte-identical counts, which is not a plausible independent-signal result -- almost certainly the comparison marks every axis changed when a ROW is touched). For a window (env ZO_DELTA_SINCE) read score_change_events via :8772/query and report per axis {axis, rows_touched, values_actually_moved, moved_pct} where values_actually_moved compares prev_label_index against new_label_index and counts ONLY genuine differences, plus a top-level finding ROW_KEYED_SUMMARY when >= 3 axes report rows_touched equal within 1 pct while values_actually_moved differs materially between them. Zero events => NO_CHANGES, an honest answer. This distinguishes "the model re-rated half the corpus" from "half the corpus was re-imported" -- currently indistinguishable, and one of those is a finding while the other is a no-op. Exemplar: `score_change_delta_report.py`. ACCEPTANCE: `__main__` on synthetic events where 5 axes are touched on identical row sets but only one axis has differing label indices asserts ROW_KEYED_SUMMARY=true and values_actually_moved is non-zero for exactly one axis; prints PASS.

- directive candidate: `deferred_router_ledger_report.py` -- the consumer that keeps today's new escape hatch honest. Read `tools/reachability_deferred.json` and the ratchet census and report per entry {module, reason, declared_since_days, still_orphan, route_count} plus totals {active, stale, reasonless, oldest_days, over_review_cap} where over_review_cap fires above 40 active deferrals. The hatch was armed on 2026-07-21 precisely because the builder cannot mount; the failure mode it invites is that "deferred" quietly becomes the new graveyard with a nicer name, which is exactly what happened to the last list that let work be postponed without a date. A deferral older than env ZO_DEFERRAL_MAX_AGE_DAYS (default 14) must be reported as AGED. Read-only, stdlib only. Exemplar: `ghost_retry_burn_report.py`. ACCEPTANCE: `__main__` on a synthetic deferred file with one fresh entry, one 30-day-old entry and one naming a module absent from the census asserts AGED fires for exactly one, stale=1, and that a reasonless entry is reported rather than dropped; prints PASS.

**PHASE 10 note for the architect -- the mount question is CLOSED for now.** The reachability ratchet was armed to `--enforce` on 2026-07-21 with the baseline pinned at the then-current level (277 as of merge) and a declare-or-mount hatch at `tools/reachability_deferred.json`. Directives that propose wiring routers into `app/main.py` (`wire_high_value_routers_into_main`, `integrate_app_routers_into_main`, and their siblings -- proposed at least three times and refused each time) remain OUT OF LANE and will keep being refused: the builder has no write access to `app/main.py` and the `module_from_exemplar` lane guard forbids self-mounting, so such a directive cannot be built no matter how well it is written. The correct move for a new router is to DECLARE it in the deferred file with a one-line reason. The mount lane itself is a human decision docketed for 2026-07-23; proposing it again before then burns a build attempt that `ghost_retry_burn_report.py` will simply count as waste.

**PHASE 11 lane (chairman daily-review spec extension 2026-07-22: the SOA carve-up input. Context: PHASE 10's 7 lanes were fully built and merged 2026-07-21; today at 12:06Z the gaps map reached proposed=0 pending=0 EXHAUSTED with the runtime current (1 commit behind origin/main), i.e. GENUINE exhaustion, not a stale-checkout false-empty. When the architect did run it emitted its propose_directive calls as PROSE (nvidia non-convergence) and proposed net-new routers + a wire task -- exactly the class the armed ratchet rejects and the 2026-07-23 mount-lane review will reshape -- so this refill is human-authored on purpose. A Council of Claudes (3 seats + FATHER, 2026-07-22) ruled: refill ONLY with REPORT-ONLY / no-net-new-router targets that WRITE a persisted artifact and feed the 07-23 review; NEVER net-new routers (the publisher auto-declares them into tools/reachability_deferred.json, now 11 and climbing toward the 40 reopen-trigger; FU-069 rebuilds the same URL under filename-dedup); and HOLD rather than mint a duplicate of an already-built observability surface. Exactly one target survived the fail-loud dedup scan against app_surface_kl / spine_manifest / unmounted_router_census_report / orphan_router_caller_probe / deferred_router_ledger_report and the seven PHASE 10 lanes. All read paths never touch the network and never import a scanned module; absence of an input is a finding (UNKNOWN), never a blank.)**

- directive candidate: `service_extraction_candidate_report.py` -- the input the 2026-07-23 mount-lane review needs and that no existing surface emits: how to carve the existing router modules into SERVICES. The review's hardest manual task under FU-072 is grouping the ~276 orphan modules + ~33 live mounts into `services/active/` dirs; `app_surface_kl` reports the routes and `spine_manifest` reports the live mounts, but neither proposes the grouping. Read the app-surface KL artifact (env ZO_APP_SURFACE_KL default `graphify-out/app_surface_kl.json`; the report NEVER makes a network call and NEVER imports a scanned module) and cluster every router module by its declared route PREFIX, reporting per group {prefix, module_count, mounted_count, orphan_count, duplicate_route_paths, suggested_service_dir} and top-level totals {groups, ungroupable_modules, largest_group, duplicate_prefix_collisions}. Modules declaring NO prefix (the ~197 no-prefix modules app_surface_kl counts) MUST be reported under a distinct `UNGROUPABLE` bucket and NEVER folded into a real service group -- their service membership is genuinely unknown, the same discipline `orphan_router_caller_probe` applies to callability. Suggested_service_dir is a derived string proposal only (e.g. `services/servers`); the report MOUNTS nothing, writes no `app/main.py`, and creates no `services/` directory -- the review decides adoption. Absence or an empty routes section in the KL artifact => top-level verdict UNKNOWN, never an empty grouping reported as complete. Read-only, stdlib only, writes `graphify-out/service_extraction_candidates.json`. Exemplar: `unmounted_router_census_report.py`. ACCEPTANCE: a `__main__` block over a synthetic in-memory KL of 5 modules (three sharing prefix `/servers`, one `/orgs`, one declaring no prefix) asserts groups=2, the `/servers` group reports module_count=3, the no-prefix module lands in UNGROUPABLE and appears in no real group, and a KL missing its routes section yields verdict UNKNOWN (not an empty result); prints PASS.


**PHASE 12 lane (chairman daily-review spec extension 2026-07-23: the mount-lane review's per-router triage. Context: PHASE 11's single lane (service_extraction_candidate_report) was built and merged 2026-07-22 (#1737); today the gaps map is again proposed=0 pending=0 EXHAUSTED with the runtime current on origin/main -- the daily ~1-anchor/day burn (FU-009/FU-065), not a stale-checkout false-empty. Today IS the 2026-07-23 mount-lane design review (FU-039/FU-064/FU-072). A Council of Claudes (3 seats + FATHER, 2026-07-23, recorded in chairman_briefing_2026-07-23.md) ruled the review: mechanism = services/active folder-scan with Option-B BUILD-TIME generation of a static fail-loud mount file (spine_manifest.py is the landed report-only reference); staged->active promotion = human-gated FIRST COHORT (mirroring the ratchet's observe->enforce); auto_declare duplicate-refusal = DEFER hard-refuse (warn/report first, flip only after a human-reviewed trial cohort proves zero false positives -- and auto_declare is recipe/config class, outside the unattended envelope regardless); and the actual mount / mount-behind-auth / DELETE of the ~20 deferred routers is DEFERRED to an attended session. What the review OWES itself before that attended session is the triage DATA per deferred router, which no existing surface emits: deferred_router_ledger_report counts and thresholds the graveyard (over_review_cap at 40) but assigns no per-router verdict; app_surface_kl and spine_manifest report routes/mounts/duplicates but propose no disposition. This refill is human-authored under the same CofC report-only rule as PHASE 11: report-only, no net-new router, one target, survived a fail-loud dedup scan against the 1,979 root modules + app_surface_kl / spine_manifest / service_extraction_candidate_report / deferred_router_ledger_report / orphan_router_caller_probe. All read paths never touch the network and never import a scanned module; absence of an input is a finding (UNKNOWN), never a blank.)**

- directive candidate: `deferred_router_triage_report.py` -- the per-router disposition the 2026-07-23 mount-lane review needs for the ~20 auto-declared routers in the deferred graveyard and that no existing surface emits. Read the deferred file (env ZO_REACHABILITY_DEFERRED default `tools/reachability_deferred.json`), the app-surface KL (env ZO_APP_SURFACE_KL default `graphify-out/app_surface_kl.json`, for `routes.taken_paths` / duplicate paths / consumer counts) and the spine manifest (env ZO_SPINE_MANIFEST default `artifacts/spine_manifest.json`, for duplicate routes among LIVE mounts); the report NEVER makes a network call and NEVER imports a scanned module. For EACH deferred router emit a verdict in {MOUNT, MOUNT_BEHIND_AUTH, DELETE, UNKNOWN}: DELETE when its declared route path collides with an already-taken path (a duplicate route -- declaring a duplicate is the ratchet blessing exactly what it exists to stop) OR it declares no route prefix (no customer contract, the ~197 no-prefix class); MOUNT_BEHIND_AUTH when its route is unique BUT it reads the data layer (name/description evidence that it exposes registry/scoring rows -- a conservative "needs an auth decision" bucket, never auto-mounted); MOUNT when its route is unique and it exposes no data-layer read; UNKNOWN when the inputs needed to decide are absent for that entry. A router whose disposition inputs are missing MUST report UNKNOWN and MUST NOT be silently defaulted to MOUNT -- the same absence-is-a-finding discipline orphan_router_caller_probe applies to callability. In the SAME pass, reuse deferred_router_ledger_report's `over_review_cap` threshold (env ZO_DEFERRED_REVIEW_CAP default 40) and emit a top-level cadence `alert` object {deferred_count, cap, over_cap: bool, trigger: "FU-064"} so the 40-entry reopen-trigger is surfaced on every run instead of being noticed only by a hand-download (the exact blindness FU-064 was armed against). Report top-level totals {deferred_count, mount, mount_behind_auth, delete, unknown, alert}. The report MOUNTS nothing, writes no `app/main.py`, deletes no module, and creates no `services/` directory -- it produces the DECISION DATA the attended review acts on. Read-only, stdlib only, writes `graphify-out/deferred_router_triage.json`. Consumer: the daily chairman review digest and the pr-gates report step. Exemplar: `deferred_router_ledger_report.py`. ACCEPTANCE: a `__main__` block over a synthetic in-memory set of deferred routers -- one whose path duplicates a taken_path, one declaring no prefix, one with a unique path that reads the data layer, one with a unique path and no data-layer read, and one missing its route info -- asserts verdicts DELETE, DELETE, MOUNT_BEHIND_AUTH, MOUNT, UNKNOWN respectively, and that a deferred set of length >= 40 yields alert.over_cap true while a short set yields false; prints PASS.


**PHASE 13 lane (chairman daily-review spec extension 2026-07-24: registry ingest-integrity tripwire. Context: PHASE 12's single lane (deferred_router_triage_report) was built and merged 2026-07-23 (#1752); today the gaps map is again proposed=0 pending=0 EXHAUSTED with the runtime current on origin/main -- the daily ~1-anchor/day burn (FU-009/FU-065), not a stale-checkout false-empty. Trigger event: overnight the prod registry doubled 232,245 -> 462,751 rows (+230,506 in one window). The nightly mcplookup-db-backup guard watches only axis-score DROPS, so this large registry GROWTH passed silently (FU-087); the chairman review had to run a MANUAL prod query to prove it was real discovery (230,506 fully-distinct rows -- distinct names AND urls -- only 39 overlapping any pre-existing name) rather than a duplicate-insert regression of the recurring dup-phantom class (server_identity_url_collision, ScoreWave2 URL-dups). The FU-088 CofC (3 seats + FATHER, 2026-07-24, recorded in chairman_briefing_2026-07-24.md) raised CADENCE_REINDEX_MAX_ROWS to 1_000_000 as a stopgap and recorded a guard-rail: keep registry growth-rate ALARMED and add a mass-reinsert tripwire. No existing surface emits that signal: registry_growth_progress_api reports total/growth_rate_7d/assessed_pct/source-breakdown (LEVELS, no anomaly verdict, no alert); scoring_coverage_audit_api / scoring_gap_analysis_api report assessment coverage/gaps; registry_source_health_report / registry_source_freshness_report report source staleness -- none classify a single-window insert spike as REAL vs SUSPECT_REINSERT. Report-only under the same CofC rule as PHASE 11/12: no net-new router, no mount, one target, survived a fail-loud dedup scan against the 1,979 root modules + registry_growth_progress_api / scoring_coverage_audit_api / scoring_gap_analysis_api / registry_source_health_report / anomaly_detector. Absence of an input is a finding (UNKNOWN), never a blank.)**

- directive candidate: `registry_ingest_anomaly_report.py` -- the ingest-integrity tripwire the 2026-07-24 registry doubling proved missing: classify a single-window registry insert spike as REAL vs a duplicate/mass-reinsert regression, the exact forensic the chairman ran by hand for FU-087, so the next doubling is auto-triaged instead of passing silently past the nightly drop-only guard. Core logic is a PURE function classify(rows, window_hours, spike_cap) over an iterable of row dicts {server_id, name, url, first_seen, registry_source} -- it makes NO network call and does NOT import a scanned module; a thin read-only adapter feeds it registry rows (env-gated, mirroring registry_source_health_report data read). Partition rows into the RECENT cohort (first_seen within env ZO_REGISTRY_ANOMALY_WINDOW_H, default 36) and the PRIOR set; compute recent_count, distinct_name, distinct_url over the recent cohort, and overlap_prior = count of recent rows whose name OR url already exists in the prior set (the re-insert signal). Emit a top-level verdict in {REAL_GROWTH, SUSPECT_REINSERT, NO_SPIKE, UNKNOWN}: NO_SPIKE when recent_count <= spike_cap (env ZO_REGISTRY_SPIKE_CAP, default 100000); SUSPECT_REINSERT when recent_count > spike_cap AND (distinct_ratio = min(distinct_name,distinct_url)/recent_count < env ZO_REGISTRY_DISTINCT_MIN default 0.90 OR overlap_prior/recent_count > env ZO_REGISTRY_OVERLAP_MAX default 0.10); REAL_GROWTH when recent_count > spike_cap AND distinct_ratio high AND overlap low; UNKNOWN when first_seen is absent on the rows (cannot window) -- and UNKNOWN MUST NOT be silently defaulted to REAL_GROWTH, the same absence-is-a-finding discipline orphan_router_caller_probe applies to callability. Also emit per-registry_source recent counts and a cadence `alert` object {recent_count, spike_cap, over_spike: bool, verdict, distinct_ratio, overlap_ratio, trigger: "FU-087"} so a spike is surfaced on every run. On today data the report MUST classify REAL_GROWTH (recent_count=230506, distinct_ratio~1.0, overlap~0.0). The report MOUNTS nothing, writes no app/main.py, adds no route -- it produces the DECISION DATA the nightly backup + daily chairman review consume; ratchet delta is 0. Read-only, stdlib for the pure core, writes `graphify-out/registry_ingest_anomaly.json`. Consumer: mcplookup-nightly-db-backup guard + the daily chairman review digest. Exemplar: `registry_source_health_report.py`. ACCEPTANCE: a `__main__` block over a synthetic in-memory registry -- a prior set of 5 rows plus a recent cohort of (case A) 120000 fully-distinct rows with zero prior overlap => verdict REAL_GROWTH and alert.over_spike true; (case B) 120000 rows that are 50% re-inserts of prior names => verdict SUSPECT_REINSERT; (case C) 10 distinct recent rows => verdict NO_SPIKE and alert.over_spike false; (case D) rows missing first_seen => verdict UNKNOWN (not REAL_GROWTH) -- asserts each verdict and prints PASS.

**PHASE 14 lane (chairman doctrine 2026-07-24 -- AUTOPOIESIS: the loop sets its own signal thresholds. Per AUTOPOIESIS.md the system's product is itself and the substrate is protean (alias rungs, no vendor pins); there is NO chairman quality-gate on matching heuristics -- the numeric thresholds written into the two targets below ARE the decision, made by the loop and tuned by the loop thereafter, never referred out. Context: with the moat now 100% distinct-URL scored (278,026 servers) the 7 LLM axes are dense, so the DIFFERENTIATOR is the deterministic security axis has_known_cve -- the sparsest signal we hold. Prod read 2026-07-24: vuln_advisories=221,885 but vuln_links=613 across only 298 servers in 189 canonical_family groups; the linker matches ONLY package_exact + repo_exact. Two deterministic, report-only CANDIDATE generators close the gap; each writes a candidate-link artifact that the gated vuln write-path applies -- never a direct builder write to vuln_links. Report-only, ratchet delta 0, survived a fail-loud dedup scan vs deterministic_signal_report / vuln_facet_extension / vuln_links / perspective_query_api. Absence of an input is a finding, never a blank.)**

- directive candidate: `cve_family_propagation_candidates.py` -- the free CVE lift the URL-dup structure hands us and no surface exploits: dups sharing a canonical_family share the underlying server, hence its advisories, but has_known_cve is computed per server_id from vuln_links, so a dup reads "no CVE" while its canonical sibling is flagged. Read vuln_links (server_id, advisory_id, match_confidence, match_basis) and mcp_server_registry (server_id, canonical_family) read-only via the app session (mirroring registry_source_health_report; NEVER a network call, NEVER imports a scanned module). For each canonical_family containing >=1 vuln-linked server, take the UNION of advisory_ids across its linked members and emit, for every family member NOT already present in vuln_links for that advisory, a candidate {server_id, advisory_id, match_basis:"family_propagation", match_confidence:0.75 (a propagated link is deliberately one notch below a direct match -- the loop's fixed value), source_family:canonical_family, propagated_from:<linked sibling server_id>}. NEVER propagate into a family with zero linked members (absence is a finding). Emit totals {families_with_cve, direct_linked_servers, propagated_new_servers, propagated_new_links}; on today's data this MUST report ~189 families, ~298 direct, ~398 propagated_new_servers. Writes graphify-out/cve_family_propagation_candidates.json for the gated vuln write-path to apply; MOUNTS nothing, writes no app/main.py, adds no route (ratchet delta 0), stdlib + read-only. Consumer: the vuln apply job + the daily chairman digest. Exemplar: deterministic_signal_report.py. ACCEPTANCE: a `__main__` over a synthetic in-memory set -- family FA {s1 linked to advisory X; s2, s3 unlinked}, family FB {s4, s5 both unlinked} -- asserts exactly 2 candidates (s2->X, s3->X) at basis family_propagation confidence 0.75, propagated_new_servers=2, and FB yields zero; prints PASS.

- directive candidate: `cve_linker_v2_candidates.py` -- the linker upgrade that turns 221,885 advisories into more than 613 links: today's linker matches only package_exact + repo_exact, so ~99.9% of the corpus never links. Read vuln_advisories (advisory_id, affected package name(s), affected version range(s) in OSV/semver form, aliases) and mcp_server_registry (server_id, url, name, and the package/repo/version/dependencies carried in metadata) read-only; deterministic ONLY -- no network, no LLM, no external resolver. Add three matchers beyond the exact pair, each emitting {server_id, advisory_id, match_basis, match_confidence} with a FIXED loop-set confidence (these values ARE the decision, tuned by the loop, never referred to the chairman): (1) version_range -- the server's declared version satisfies the advisory's affected semver range => basis "version_range", confidence 0.90, comparison via a vendored pure-python semver comparator (packaging.version or a small stdlib comparator), never a network resolver; (2) dependency -- the server declares a dependency whose name equals the advisory's affected package => basis "dependency", confidence 0.60; (3) alias -- normalized-name match after case-fold + strip of ecosystem prefix ("npm:","pypi:","gh:"), npm scope ("@org/"), and ".git" suffix, matched against the advisory package name OR its aliases => basis "alias", confidence 0.80. Emit a candidate ONLY when it is not already in vuln_links AND confidence >= min_link_confidence=0.60 (loop-set floor). Report per-basis counts + total; writes graphify-out/cve_linker_v2_candidates.json for the gated vuln write-path to apply. A missing version/dependency field is a finding for that pair (skip, count under "insufficient_evidence"), never a fabricated match. MOUNTS nothing, adds no route (ratchet delta 0), stdlib + read-only. Exemplar: deterministic_signal_report.py. ACCEPTANCE: a `__main__` over synthetic advisories/servers -- advisory {pkg "foo", range ">=1.0,<2.0", aliases ["npm:foo"]} with servers {foo@1.5 => version_range/0.90, foo@3.0 => no match, "@scope/foo" with no version => alias/0.80, a server whose deps include "foo" => dependency/0.60}, plus one server missing its version counted under insufficient_evidence not matched -- asserts each basis+confidence and that below-floor/duplicate candidates are dropped; prints PASS.

**PHASE 11 lanes (chairman spec extension 2026-08-02: the factory's own unit became unbuildable and nothing said so. Context: the generator hit its STARVATION FLOOR at 12:02:24Z -- "gaps map is EXHAUSTED ... The queue stays empty and the builder stays idle. This needs a human" -- with proposed=0 pending=0 and goose_runner logging "No eligible directives found" for 152 consecutive cycles. That is the visible half. The invisible half, found the same run: at 09:10:49Z the four filenames that CONSTITUTE the service unit -- service.toml, __init__.py, router.py, logic.py -- were quarantined as missing_on_disk while 360/334/345/327 copies respectively sat under services/, because the quarantine keyspace is a BASENAME and the recovery sweep probed only <root>/<filename>, a path a service-unit member never occupies. may_rebuild returned False for all four and validate_directive refuses any blocked output_file, so the atomic unit of the loop was unbuildable in code, permanently, and no surface reported it. Fixed in #2689 and released on the runtime the same run. Every lane below makes one of this morning's blind spots MEASURABLE rather than adding a gate over it (HARNESS_DOCTRINE R7: prefer recovery over restriction). This refill deliberately REJECTS the four near-identical per-daemon liveness modules the FU-009 drafter proposed -- four single-file reports differing only by a service name is the file-unit reflex the autopoiesis doctrine names as the vanity trap; they are collapsed into ONE target below. All read paths via :8772/query; an unobservable count is UNKNOWN, never 0 (R6).)**

- directive candidate: `daemon_liveness_report.py` -- ONE report over EVERY service declared in KNOWN_DAEMONS, not one module per daemon: emit {service, last_seen_age_sec, sla_sec, status in ALIVE|STALE|NEVER_SEEN|UNKNOWN} for all of them from the real heartbeat rows via :8772/query, plus a roster-coverage line naming any daemon observed in `ps` but absent from KNOWN_DAEMONS and vice versa. REPORT-ONLY: never restarts, signals or spawns. Motivating incident it must reproduce (a detector's first proof is the incident that motivated it): on 2026-08-02 `rug_pull_monitor` read age=1078h and `sentinel_directive_generator` age=806h while the generator was demonstrably invoking goose every 10 minutes -- so a STALE heartbeat and a dead daemon are DIFFERENT claims and the report must not conflate them. A daemon with zero heartbeat rows ever => NEVER_SEEN; a heartbeat that cannot be read => UNKNOWN; neither is ALIVE. Exemplar: `dashboard_summary_api.py`. ACCEPTANCE: __main__ asserts, on synthetic rows, that a heartbeat older than the SLA => STALE, a missing row => NEVER_SEEN, an unreadable source => UNKNOWN (and specifically NOT STALE), and that a daemon present in `ps` but absent from the roster is reported; prints PASS.

- directive candidate: `quarantine_keyspace_collision_probe.py` -- read `gate_quality_state.json` and report every quarantine/retry key that is a NON-UNIQUE basename, i.e. a key K for which more than one distinct path under the repo ends in K: emit {key, distinct_paths_n, sample_paths, verdict in UNIQUE|COLLIDING}. A colliding key means one service's failure increments a counter that gates EVERY service. This probe must reproduce its own motivating incident: fed the 2026-08-02T09:10:49Z state it must return COLLIDING for service.toml, __init__.py, router.py and logic.py, and UNIQUE for retention_sweeper.py -- a probe that cannot discriminate those two cases is measuring nothing. Read-only; never edits the state file. Exemplar: `dashboard_summary_api.py`. ACCEPTANCE: __main__ on a synthetic tree with two services/<name>/service.toml and one root-level retention_sweeper.py asserts COLLIDING for the former and UNIQUE for the latter, and asserts a key matching ZERO paths is reported as COLLIDING=False with distinct_paths_n=0 rather than being silently dropped; prints PASS.

- directive candidate: `directive_queue_starvation_timeline.py` -- a durable per-day series of factory starvation: for each day, minutes during which the effective directive queue (proposed + pending, excluding .done.json/.failed.json) was ZERO, the count of "No eligible directives found" observations, and first/last idle timestamps. Motivating incident: on 2026-08-02 the builder idled from 09:59:36Z with the ONLY record being an ERROR line in the generator log, and goose_runner.log has been TRUNCATED three days running (FU-200), so a cumulative read is not a rate. Therefore the series must be written to a durable rollup that is max-over-observations per day and idempotent on re-run, never recomputed from the live log alone. A day with no observations => UNKNOWN, never 0 idle minutes. Exemplar: `dashboard_summary_api.py`. ACCEPTANCE: __main__ asserts a second run over the same input raises no day's value and adds no duplicate row (idempotent), that a day with zero observations reports UNKNOWN rather than 0, and that a truncated log whose window opens mid-day is reported PARTIAL with its window start; prints PASS.

- directive candidate: `service_unit_promotion_readiness_report.py` -- per STAGED service, the single reason it is not promotable, as a report rather than a promoter log grep: {service, blocking_reason in COPY_MISSING|NO_TOML|NO_ROUTER|ROUTE_COLLISION|CONTRACT_UNRUN|READY, tracked bool}. The CONTRACT_UNRUN bucket is the point: the liveness contract has been EXECUTED 0 times for six consecutive days, and contract_ok=0 alongside contract_FAILED=0 is ZERO MEASUREMENT, not zero failures -- the report must render those as distinct values and must never sum them into a pass rate (R3). Now load-bearing because the Dockerfile COPY wall closed (FU-217) and T2 asks for a first autonomous promotion by 2026-08-08. Read-only; never promotes. Exemplar: `dashboard_summary_api.py`. ACCEPTANCE: __main__ on synthetic services asserts a service whose contract never ran is CONTRACT_UNRUN and is NOT counted as either pass or fail, that a service satisfying every condition is READY, and that a service with two blocking reasons reports the deterministic higher-precedence one rather than an arbitrary pick; prints PASS.

- directive candidate: `mcp_definition_history_pipeline_gap_report.py` -- read-only pipeline-gap report over the real `mcp_definition_history` table via :8772/query: {table, rows, status in EMPTY|POPULATED|UNKNOWN} plus hours since the newest row, so an empty core table is a standing REPORT rather than a chairman's rediscovery. Measured 2026-08-02: 0 rows, alongside four other empty core tables. A count that cannot be observed => UNKNOWN, never OK. Markdown + JSON to stdout. Exemplar: `dashboard_summary_api.py`. ACCEPTANCE: __main__ on a synthetic empty table asserts EMPTY, on one seeded row asserts POPULATED, and on an unreachable query surface asserts UNKNOWN (and specifically not EMPTY, since an unreadable table and an empty one are different claims); prints PASS.

- directive candidate: `cve_axis_freshness_report.py` -- THE LINE, made queryable for the axis we sell as the differentiator: for the CVE/vuln surface report {feed, newest_published_at, newest_fetched_at, age_hours, rows, status in FRESH|STALE|ABSENT|UNKNOWN} against a stated freshness budget, so no signed or keyed surface can be served off stale advisory data without that being visible. Must state the BASIS with every number -- units, window and source (R5). Note for the builder: `vuln_advisories` and `vuln_links` raised Catalog Error on the tower DuckDB on 2026-08-01, so ABSENT is a REAL and expected outcome and must be reported as ABSENT, never as 0 rows and never as FRESH. Exemplar: `dashboard_summary_api.py`. ACCEPTANCE: __main__ asserts a missing table => ABSENT, an advisory older than the budget => STALE, a fresh one => FRESH, and an unreadable surface => UNKNOWN; asserts every emitted number carries its unit and window; prints PASS.

## PHASE refill 2026-08-31T04:10Z (directive candidates, NOT YET BUILT)

*Refilled by daily-chairman-review (ACT-AUTHORITY: refill a starved anchor) after the deterministic seeder reported the gaps map EXHAUSTED on a current checkout (HEAD==origin/main 7ef571d7, 2026-08-31 03:39Z). Targets chosen from live evidence: chairman issues #4002/#4080, the ASK/CVE population half (retrieval landed PR #3913), student-adapter wiring, and the scaffold-before-module red cohort observed on PR #4258 tonight. Service-unit phrasing; deterministic matching only.*

- directive candidate: `cve_population_backfill_job.py` -- Drive the STAGED nvd/ghsa ingestor outputs through the grounded engine path into the prod advisory tables (population half of ASK/CVE; retrieval half landed PR #3913). Deterministic identifiers only; every row carries source_url, fetched_at, match_confidence, feed (osv|ghsa|nvd); idempotent re-run is a no-op. Exemplar: `osv_feed_ingestor.py`. [anchor-refill: chairman-review 2026-08-31; ask_cannot_see_cves memory]
- directive candidate: `vuln_linkage_coverage_report_api.py` -- Service unit (router+logic+service.toml) reporting per-feed advisory linkage coverage against the registry: rows linked, rows unlinkable, basis timestamp per feed. Publishes the BASIS with every number (R5); zero rows is a value, not an error. Exemplar: `dashboard_summary_api.py`. [anchor-refill: chairman-review 2026-08-31]
- directive candidate: `student_adapter_registry.py` -- Read the sibling repo contract (rob531/zomesh-sentinel-sft leaderboard/sentinel_student.json): select the adapter with bar_passes=True, else highest composite; expose adapter id, branch, and acceptance-bar status as a service. Consume `schemas/risk_axis_mapping_v1.json` VERBATIM (auth_strength has 4 classes, never infer enums from samples). Exemplar: `runtime_deploy_info_endpoint.py`. [anchor-refill: chairman-review 2026-08-31; cross-repo contract]
- directive candidate: `risk_axis_contract_validator.py` -- Validate any scorer/UI payload against risk_axis_mapping_v1.json integer mappings for all 7 axes; reject payloads whose class enums do not match the schema verbatim. Pure function + thin service surface so both scoring consumers and UI share one validator. Exemplar: `facet_enum_service.py`. [anchor-refill: chairman-review 2026-08-31]
- directive candidate: `staged_dryrun_import_repairer.py` -- Per issue #4002 (25 staged services fail a dry-run import): walk services/staged, dry-run import each unit, classify the failure, repair the trivial classes in place (missing `__init__.py`, service.toml import_path that does not resolve), and report the rest with the failing exception per unit. Idempotent; repairs are committed via the normal PR path, never silent. Exemplar: the dry-run harness in `tests/test_dockerfile_copy_covers_active_services.py`. [anchor-refill: chairman-review 2026-08-31; issue #4002]
- directive candidate: `service_unit_atomicity_gate.py` -- Tonight 54 armed auto-merge PRs sat red because scaffolds register a service whose import_path resolves to no file in the same change-set (observed live on PR #4258: risk_tier_registry_aggregator). Add a pre-PR check in the promoter fan-out path: a directive that would register or activate a service must carry the module files that make its import_path resolve, or be split so the module lands first. Census EVERY call site of the registering shape in the same commit (one door of eight is not a cure). Exemplar: promoter fan-out in `candidate_promoter_daemon.py`. [anchor-refill: chairman-review 2026-08-31; META CAP satisfied: this class cost product output today]
