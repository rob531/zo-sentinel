# ZO-SENTINEL Development Roadmap
# sentinel_director.py reads this file on every maturity check.
# Format: filename | complexity | phase | reads (csv) | description
# Director injects a directive for any file that is missing or < 500 bytes.

## PHASE 2 — Core Schema + Scanner
schema.py | low | 2 | | DuckDB schema creation. create_all() via write_service /execute.
schema_v2.py | low | 2 | schema.py | Extended schema: mcp_submissions, mcp_decisions, mcp_policy_rules. create_v2() + seed_default_policies().
mcp_scanner.py | high | 2 | schema.py | T1 npm @modelcontextprotocol/* + GitHub topic:mcp-server crawler. ws_write to mcp_server_registry. 6hr daemon.

## PHASE 2B — Bootstrap (schema init + seed data; MUST run before Phase 3)
# These are run_script directives handled separately in bootstrap_directives.json
# schema_init: python3 /home/workspace/zo_sentinel/schema.py (creates all tables)
# seed_data: python3 /home/workspace/zo_sentinel/mcp_data_seeder.py (loads real MCPs)
mcp_data_seeder.py | high | 2 | schema.py | One-shot seeder. Fetches real MCP packages from npm registry API (https://registry.npmjs.org/-/v1/search?text=scope:modelcontextprotocol&size=250) and GitHub API (https://api.github.com/search/repositories?q=topic:mcp-server&sort=stars&per_page=50). For each: extract name, description, url, registry_source. Write to mcp_server_registry via ws_write. Target: 50-80 real servers seeded. No daemon. Exits after seeding. Requires tables to exist.

## PHASE 3 — Signal Intelligence (requires seeded data)
known_threats.py | low | 3 | | Static threat intel: KNOWN_MALICIOUS_PACKAGES, HIGH_RISK_PATTERNS (raw strings), check_package(), check_domain().
signal_analyser.py | high | 3 | schema.py,known_threats.py | T2 daemon scoring MCPs on 6 signals (domain_trust, tool_description_safety, permission_scope, supply_chain, community_signal, temporal_stability 0-100). ws_query mcp_server_registry, ws_write mcp_signal_scores. 30min daemon.
trust_synthesiser.py | high | 3 | schema.py,signal_analyser.py | T3 daemon compositing trust_score from 6 signals (weights sum to 1.0). Maps to verdict. ws_write verdict+reasoning to mcp_server_registry. 30min daemon.

## PHASE 4 — Approval Workflow
approval_workflow.py | high | 4 | schema.py,schema_v2.py,policy_engine.py | FastAPI port 8780. POST /api/submit, POST /api/decision/{id}, GET /api/registry, GET /api/audit, GET /health. All writes via write_service rows field.
policy_engine.py | medium | 4 | schema_v2.py | evaluate_policy(submission, trust_score, verdict) -> BLOCK|ESCALATE|ALLOW. Reads mcp_policy_rules via ws_query.

## PHASE 5 — Threat Monitoring
rug_pull_monitor.py | high | 5 | schema.py,known_threats.py | Monitors approved MCPs for tool definition mutations via SHA256 hash. ws_write mcp_threat_associations on change. 6hr daemon.

## PHASE 6 — Registry API
registry_api.py | high | 6 | schema.py,signal_analyser.py,trust_synthesiser.py | FastAPI port 8781. GET /v1/assess?mcp=, GET /v1/registry, GET /v1/threats, GET /health. All DB reads via ws_query.

## PHASE 7 — Threat Intel + Risk + Attestation
threat_intel_ingestor.py | high | 7 | schema.py,known_threats.py | Daemon ingesting CVEs from OSV.dev and world_articles cybersecurity feed. Writes mcp_threat_associations {server_id, threat_type, evidence, severity:CRITICAL|HIGH|MEDIUM|LOW}. 2hr daemon.
risk_ranker.py | high | 7 | schema.py,signal_analyser.py | Composite risk_rank from trust_score + threat_count + environment_exposure + staleness. Writes mcp_risk_register {server_id, name, risk_rank, risk_tier:CRITICAL|HIGH|MEDIUM|LOW}. Writes RISK_REGISTER.md. 4hr daemon.
attestation_engine.py | high | 7 | schema.py,schema_v2.py,trust_synthesiser.py | Non-binding probabilistic attestations. generate_attestation(server_id). Language never binary. Writes mcp_attestations + ATTESTATION_REPORT.md. 6hr daemon.

## PHASE 8 — Search + Lookup
search_api.py | high | 8 | schema.py,registry_api.py | FastAPI port 8782. GET /search?q= (ILIKE mcp_server_registry), GET /mcp/{server_id} (full detail), GET /threats, GET /risks, GET /health.
lookup.py | medium | 8 | schema.py | CLI: python3 lookup.py <mcp_name>. Colour-coded terminal report. --threats, --risks, --stats flags. No daemon.

## PHASE 9 — Integration + Observability
pipeline_health.py | medium | 9 | schema.py | Daemon checking pipeline health: unscored servers, stale assessments (>7d), unattestation'd servers. Writes pipeline_health events to mesh_events every 4hr.
integration_test.py | high | 9 | schema.py,registry_api.py,approval_workflow.py | End-to-end test suite. Tests full pipeline using seeded data: assess -> signal score -> verdict -> attest -> approve. Writes pass/fail to mesh_events. Run via: python3 integration_test.py --write-db.

## PHASE 10 — Hardening
rate_limiter.py | medium | 10 | | Rate limiting middleware for approval_workflow and registry_api. Per-IP request tracking in DuckDB.
error_reporter.py | medium | 10 | schema.py | Aggregates build_failed and smoke_fail events. Writes daily ERROR_REPORT.md. 24hr daemon.
config_validator.py | low | 10 | schema.py,schema_v2.py | Validates ZO-SENTINEL config at startup: tables exist, write_service reachable, ports open, env vars set. Returns pass/fail dict.

## PHASE 11 — Extended APIs
email_guid_auth.py | medium | 11 | schema.py | Email + GUID magic-link authentication for analyst portal. FastAPI port 8783. POST /auth/request-link, GET /auth/verify?token=. Stores auth_tokens via ws_write. TTL 24h. run() daemon.
advanced_filter_api.py | medium | 11 | schema.py,registry_api.py | Advanced filtering API port 8784. GET /filter with verdict, score range, threat_type, date_range params. Queries mcp_server_registry + mcp_threat_associations via ws_query. Paginated.
forensic_detail_api.py | medium | 11 | schema.py,signal_analyser.py | Deep forensic detail port 8785. GET /forensics/{server_id}: full signal history, threat timeline, hash mutations, attestation history, approval decisions. All via ws_query.
manual_override_api.py | medium | 11 | schema.py,schema_v2.py | Analyst override API port 8786. POST /override/{server_id} with verdict+reason+duration_days. GET /overrides lists active. Audit to mcp_decisions via ws_write.

## PHASE 12 — Exports + Compliance
compliance_export_service.py | medium | 12 | schema.py,schema_v2.py | Compliance export daemon. Generates COMPLIANCE_REPORT.md + compliance_export.json every 24h. Reads mcp_server_registry, mcp_decisions, mcp_risk_register. ws_write heartbeat. run() daemon.
sentinel_status.html | high | 12 | | Single-page HTML status dashboard. Shows pipeline health, registry stats, recent threats, latest attestations, last 10 builds. Polls /api/search + /v1/registry + /v1/threats every 30s. Dark brutalist style. Self-contained.

## PHASE 13 — Mesh Integration
intent_to_directive_bridge.py | medium | 13 | | Daemon bridging intent engine anticipations to builder directives. Reads high-urgency anticipations from anticipations table. Translates security/build domain items to directive JSON. Ensures anticipations table exists. 15min poll. run() daemon.
mesh_sentinel_reporter.py | medium | 13 | schema.py | Reports ZO-SENTINEL statistics to ZOMesh memory every 6h. Reads registry counts, verdict distribution, threat counts. Writes mesh_memory entries for other agents to consume. run() daemon.

## PHASE 14 — Quality Passes (rebuild core modules with full context)
signal_analyser_v2.py | high | 14 | schema.py,known_threats.py,signal_analyser.py | Improved signal analyser with Bayesian confidence weighting across all 6 signals. Adds temporal decay (older scores weighted less). Writes confidence_interval alongside score. 30min daemon.
trust_synthesiser_v2.py | high | 14 | schema.py,signal_analyser.py,trust_synthesiser.py | Improved trust synthesiser with dynamic weight adjustment based on server type (npm vs github vs manual). Adds VERDICT_CHANGED event to mesh_events when verdict flips. 30min daemon.