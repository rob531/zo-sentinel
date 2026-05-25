# ZO-SENTINEL Architecture Documentation

*Last updated: 2026-05-24*

---

## Overview

ZO-SENTINEL is an autonomous intelligence pipeline that assesses Model Context Protocol (MCP) servers, enriches them with threat intelligence, and assigns a deterministic security verdict. It operates as a mesh of cooperating daemons that share state exclusively through a central write_service.

**Primary user:** CISO / Security Architect looking up an MCP by name in the Search-Driven UI (port 8790) to make a deployment decision.

**Scope posture:** Sentinel is an *intelligence layer*. It produces trust signals, verdicts, and detection artefacts. It does NOT route MCP traffic, authenticate users, enforce policy at call time, or block/throttle traffic.

---

## 1. Verdict Taxonomy

Six tiers plus one data-gap state. The trust synthesiser is calibrated for these exact states.

| Verdict | Composite Score | Meaning |
|---------|-----------------|---------|
| `TRUSTED_GENERAL` | > 75 | Approved for general enterprise use |
| `TRUSTED_RESEARCH` | > 60 | Safe for research / exploratory use |
| `ENTERPRISE_CONTROLLED` | > 45 | Acceptable with documented security controls |
| `CAUTION_LIMITED` | > 30 | Requires additional review |
| `HIGH_RISK_ISOLATED` | > 15 | Sandboxed environments only |
| `KNOWN_THREAT` | ≤ 15 | Matched known-threat signal |
| `INSUFFICIENT` | — | ≥5 of 8 signals missing (data-gap state, not a risk tier) |

---

## 2. Signal Model

Eight signals feed the composite score. Every signal producer writes rows to `mcp_signal_scores` or `mcp_signal_enrichments` with this invariant shape:

```json
{
  "signal_type": "<snake_case_name>",
  "confidence": 0.0-1.0,
  "evidence_blob": { ... }
}
```

| Signal | Table | Description |
|--------|-------|-------------|
| `domain_trust` | mcp_signal_scores | DNS reputation, SSL validity, age |
| `tool_description_safety` | mcp_signal_scores | Description length, safety keywords, risky patterns |
| `permission_scope` | mcp_signal_scores | Scope breadth and risk level |
| `supply_chain` | mcp_signal_scores | Registry source, publisher, npm/GitHub signals |
| `community_signal` | mcp_signal_scores | GitHub stars, npm downloads, forum mentions |
| `temporal_stability` | mcp_signal_scores | Version change frequency, staleness |
| `supply_chain_enrichment` | mcp_signal_enrichments | Ecosystem metadata (npm/GitHub/PyPI) |
| `community_signal_enrichment` | mcp_signal_enrichments | Extended community metrics |

**Enricher contract (PRODUCT_SPEC §3):** pure function `compute_score(metadata: dict) -> (float in [0,100], evidence dict)`. No DB writes, no network, no imports of protected modules. Evaluated by `enrichment_harness.py` against a synthetic corpus; rejected if it yields fewer than 20 distinct scores across 34 fingerprints.

---

## 3. Core Loop

```
mcp_scanner / mcp_directory_ingestor
        │
        ▼
mcp_discovery_feeder / mcp_registry_ingestor_v2
        │
        ▼
signal_analyser + enrichment modules (8 signals)
        │
        ├──► mcp_signal_scores / mcp_signal_enrichments
        │
        ▼
trust_synthesiser / trust_synthesiser_v2 → verdict
        │
        ▼
attestation_engine → mcp_attestations
        │
        ▼
risk_ranker → mcp_risk_register
        │
        ▼
UI (8790) + external API (8791)
```

---

## 4. Daemon Topology

| Service | Port | File | Responsibility |
|---------|------|------|----------------|
| write_service | 8772 | write_service.py | Central DuckDB write/read/execute hub |
| inference_router | 8773 | inference_router.py | LLM inference (direct call, not peer HTTP) |
| mcp_scanner | — | mcp_scanner.py | Periodic registry polling, server probing |
| mcp_directory_ingestor | — | mcp_directory_ingestor.py | Scans modelplatforms.ai and directories |
| mcp_discovery_feeder | — | mcp_discovery_feeder.py | Processes discovery candidates → registry |
| signal_analyser | — | signal_analyser.py | Computes 6 core signals per server |
| signal_analyser_v2 | — | signal_analyser_v2.py | Enhanced analyser with v3 enrichments |
| trust_synthesiser | — | trust_synthesiser.py | Weighted composite → verdict |
| trust_synthesiser_v2 | — | trust_synthesiser_v2.py | 7-dimension trust synthesis |
| attestation_engine | — | attestation_engine.py | Generates non-binding attestations |
| threat_intel_ingestor | — | threat_intel_ingestor.py | OTX/Alienvault pulse ingestion |
| risk_ranker | — | risk_ranker.py | Threat overlay → risk tier |
| assessment_scheduler | — | assessment_scheduler.py | Enforces freshness SLAs |
| rug_pull_monitor | — | rug_pull_monitor.py | Detects npm/package name hijacking |
| approval_workflow | 8780 | approval_workflow.py | Admin approval API |
| registry_api | 8781 | registry_api.py | REST CRUD for registry |
| ui_server | 8790 | ui_server.py | Search-driven dashboard UI |
| sentinel_external_api | 8791 | sentinel_external_api.py | Read-only external API, X-API-Key auth |
| pi_flagged_review_api | 8792 | pi_flagged_review_api.py | Flagged review API |
| snow_connector | — | snow_connector.py | ServiceNow inbound webhook |
| github_pr_checker | — | github_pr_checker.py | GitHub PR commit verification |
| github_pr_webhook_receiver | — | github_pr_webhook_receiver.py | GitHub webhook handler |
| sentinel_directive_generator | — | sentinel_directive_generator.py | Generates directives from signal data |
| build_watcher | 8795 | build_watcher_api.py | Directive build progress tracker |
| audit_log_writer | — | audit_log_writer.py | Admin action audit trail |
| exemption_manager | — | exemption_manager.py | Admin exemption CRUD |
| attestation_refresher | — | attestation_refresher.py | Regenerate expiring attestations |
| exemption_expirer | — | exemption_expirer.py | Deactivate past-valid_until exemptions |
| retention_sweeper | — | retention_sweeper.py | Age-based evidence_blob expiry |
| candidate_promoter_daemon | — | candidate_promoter_daemon.py | Promote discovery candidates |
| registry_promoter_daemon | — | registry_promoter_daemon.py | Promote vetted candidates to registry |
| decision_emitter_daemon | — | decision_emitter_daemon.py | Emit approval decisions |
| fingerprint_runner_daemon | — | fingerprint_runner_daemon.py | Compute MCP server fingerprints |
| gate_scheduler | — | gate_scheduler.py | Orchestrates build pipeline gates |
| gate_orchestrator | — | gate_orchestrator.py | Executes gate framework |

---

## 5. write_service API Contract

**Base URL:** `http://127.0.0.1:8772`

**Rule (PRODUCT_SPEC §5):** No HTTP between peer daemons. All state exchange goes through write_service.

### 5.1 Write
```
POST /write
{"table": "table_name", "rows": [...], "wait": true}
```
### 5.2 Query
```
POST /query
{"sql": "SELECT ... WHERE $1 = $2", "params": [...]}
```
### 5.3 Execute
```
POST /execute
{"sql": "CREATE TABLE ...", "params": []}
```

---

## 6. Enrichment Modules

Enrichers are pure functions consuming `mcp_signal_scores` rows and producing `mcp_signal_enrichments`. All live in the root directory.

| Module | Signal Type | Status |
|--------|-------------|--------|
| supply_chain_enrichment | supply_chain_enrichment | ACTIVE |
| supply_chain_enrichment_v2/v3 | supply_chain_enrichment | ACTIVE |
| community_signal_enrichment | community_signal_enrichment | ACTIVE |
| community_signal_enrichment_v2/v3/v4 | community_signal_enrichment | ACTIVE |
| temporal_stability_enrichment | temporal_stability | ACTIVE |
| temporal_stability_enrichment_v2/v3/v4 | temporal_stability | ACTIVE |
| tool_description_safety_enrichment | tool_description_safety | ACTIVE |
| tool_description_safety_enrichment_v2/v3/v4 | tool_description_safety | ACTIVE |
| permission_scope_enrichment | permission_scope | ACTIVE |
| permission_scope_enrichment_v2/v3 | permission_scope | ACTIVE |
| domain_trust_enrichment | domain_trust | ACTIVE |
| domain_trust_enrichment_v2 | domain_trust | ACTIVE |
| evidence_density_enrichment | evidence_density | ACTIVE |
| injection_resilience_enrichment | injection_resilience | ACTIVE |
| context_efficiency_enrichment | context_efficiency | ACTIVE |
| registry_breadth_enrichment | registry_breadth | ACTIVE |
| coverage_gap_reporter | coverage_gap | ACTIVE |

Signal bridge wiring: `signal_bridge.py` (65K log entries) connects enrichment outputs to signal analyser v2.

---

## 7. Freshness SLAs

| Metric | SLA |
|--------|-----|
| First verdict after `first_seen` | ≤ 24 hours |
| Re-verdict cycle | ≤ 7 days |
| Evidence blob retention | 30 days |
| Verdict / attestation / threat association | Indefinite |

Enforced by: `assessment_scheduler`, `stale_data_cleaner`, `retention_sweeper`.

---

## 8. Security Boundaries

- **External API** (8791): Read-only, `X-API-Key` auth, 60 req/min per key.
- **Internal APIs** (8790, 8780, 8781, 8792, 8795): localhost only.
- **Secrets:** API keys via `os.environ.get()`, keys file mode 0600.
- **SQL injection:** All user-supplied values via `params` arrays.
- **Audit:** Every admin-write action logged to `audit_log` with `event_type`, `actor`, `target_server_id`, `action`, `outcome`, `timestamp`.

---

## 9. Build Gates

Directives are validated through a gate pipeline before execution:

| Gate | Purpose |
|------|---------|
| Gate 1 | Infrastructure check (write_service, schema) |
| Gate 2 | Schema contracts |
| Gate 5 | Synthesis flow |
| Gate 7 | Threat flow |
| Gate 8 | New module smoke test (import, static safety, signal invariant) |
| Gate 9 | Signal diversity |

Protected files: hand-calibrated, not rebuilt directly. Companion modules are the preferred change mechanism.

---

## 10. Out of Scope (v1.0)

- Multi-tenancy / OAuth flows (only `X-API-Key` permitted)
- MCP gateway/proxy/portal functionality (traffic routing, call-time enforcement)
- Outbound webhooks to third parties
- Billing, metering, plans
- Slack/Teams/email integrations
- Grafana/Prometheus dashboards
- GraphQL surface (graphql_schema_builder is dormant)
- Retention DELETE daemons (expire by query filter, not row deletion)
- ML anomaly detection beyond existing pattern_learner.py

---

## 11. Data Architecture

**Single source of truth:** DuckDB (`data/sentinel.db`). No replication, no cross-database joins.

**Core tables (append-only or update-in-place for verdicts; NO DROP/DELETE):**
- `mcp_server_registry` — MCP server inventory
- `mcp_signal_scores` — Signal scores per server
- `mcp_signal_enrichments` — Enrichment outputs per server
- `mcp_attestations` — Attestations per server
- `mcp_risk_register` — Risk rankings
- `mcp_exemptions` — Admin exemptions
- `mcp_decisions` — Analyst decisions
- `mcp_policy_rules` — Policy rules
- `mcp_submissions` — User submissions
- `mcp_fingerprints` — Server fingerprints
- `mcp_tool_hashes` — Tool hashes
- `service_health` — Daemon heartbeats
- `audit_log` — Admin action audit trail
- `mcp_discovery_candidates` — Pre-registry candidates
- `mcp_directory_mentions` — Directory-based discoveries
- `mcp_ecosystems_metadata` — Package ecosystem data
- `github_velocity` — GitHub repo velocity data
- `mcp_definition_history` — Definition snapshots
- `build_provenance` — Directive build history
- `auth_tokens` — API auth tokens

**Awaiting-user tables (legitimately empty until admin action):**
- `mcp_submissions`, `mcp_exemptions`, `mcp_decisions`, `mcp_policy_rules`, `mcp_fingerprints`, `mcp_tool_hashes`

---

## 12. Supervisor Configuration

All daemons run under supervisord. See `supervisord_sentinel.conf` for the master config.

Key services:
- `write_service` — Always on
- `inference_router` — Always on
- `ui_server` — Always on
- `sentinel_external_api` — Always on
- `gate_scheduler` — Always on
- Scheduled daemons (assessment_scheduler, rug_pull_monitor, etc.) — `autorestart=true, startsecs=5`

---

## 13. Detection Library Modules

Pure functions (no daemons, no network, no DB) providing detection artefacts.

| Module | Purpose |
|--------|---------|
| `mcp_traffic_fingerprints.py` | Regex detection of JSON-RPC MCP traffic |
| `mcp_tool_schema_patterns.py` | Architectural pattern detection in tool definitions |
| `shadow_mcp_indicators.py` | URL/hostname pattern indicators for log analysis |
| `mcp_project_canonicalizer.py` | Canonical name normalization |

---

## 14. Integration Points

| Integration | Status |
|-------------|--------|
| ServiceNow (snow_connector) | INBOUND WEBHOOK ACTIVE |
| GitHub PR Checker (github_pr_checker) | WEBHOOK RECEIVER ACTIVE |
| AiDr Commit Gateway (aidr_commit_gateway) | VERDICT CHECK ACTIVE |
| ecosystems_metadata_fetcher | DAILY CYCLE ACTIVE |
| OTX/Alienvault threat feed | 15-MIN CYCLE ACTIVE |

---

*End of ARCHITECTURE.md*