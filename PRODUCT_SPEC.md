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
- directive candidate: `perspective_snapshot_daemon.py` -- the gate-2 cadence job: every
  SNAPSHOT_INTERVAL_HOURS (default 24) call perspective_diff_service.snapshot_perspective for
  every saved perspective, then diff_perspective to queue PerspectiveEvent rows; single-instance
  guard; heartbeat log line per cycle. Runs as a container daemon (daemon_wrapper pattern).
  ACCEPTANCE: __main__ with a fake session asserts one cycle snapshots every perspective exactly
  once and is idempotent within the interval; prints PASS.
- directive candidate: `ask_corpus_drift_guard.py` -- the gate-3 trigger: compare
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
