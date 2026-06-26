# ZO Sentinel & ZO Mesh — Migration Plan

> Generated: 2026-06-26 UTC  
> Source system: ZoComputer (`zo-mcp-server-robinc.zocomputer.io`)  
> Purpose: Map every component of ZO Sentinel, ZO Mesh, and the Builder/Directive system; define the steps to mirror them into GitHub.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [ZO Sentinel — File Structure](#zo-sentinel--file-structure)
3. [ZO Mesh — File Structure](#zo-mesh--file-structure)
4. [Builder & Directive Generator System](#builder--directive-generator-system)
5. [Storage Layer — ZO Mesh DuckDB](#storage-layer--zo-mesh-duckdb)
6. [Supporting Components](#supporting-components)
7. [Migration Plan](#migration-plan)
8. [Repo Structure (Target GitHub Layout)](#repo-structure-target-github-layout)

---

## System Overview

| Component | Path on ZoComputer | Purpose |
|-----------|-------------------|---------|
| **ZO Sentinel** | `/home/workspace/zo_sentinel/` | MCP server trust intelligence — scores, verdicts, analyst workflows |
| **ZO Mesh** | `/home/workspace/zo_mesh/` | Runtime infrastructure — watchdog, write service, inference router, pipeline |
| **Storage** | `/home/workspace/Datasets/zo-mesh/data.duckdb` | 758 MB DuckDB — all Sentinel + Mesh operational data |
| **World Agent** | `/home/workspace/world_agent/` | Autonomous news/research agent feeding mesh_memory |
| **Logs** | `/home/workspace/logs/` | All daemon logs |
| **Shared Outputs** | `/home/workspace/shared/outputs/goose/` | Build artifacts from Goose/ladder |

**Language:** Python 3.11 throughout (FastAPI for HTTP services, plain Python for daemons).  
**Note:** No TypeScript in Sentinel or Mesh core. TypeScript files exist only in `/home/workspace/Skills/` (intent engine, skill definitions).

---

## ZO Sentinel — File Structure

Root: `/home/workspace/zo_sentinel/`

### Core Framework

| File | Role |
|------|------|
| `schema.py` | Base DB schema definitions |
| `schema_v2.py` | Extended schema with Phase 8 tables |
| `DB_SCHEMA.md` | Auto-generated live DB reference (42 tables) |
| `SENTINEL_DIRECTIVE_SCHEMA.md` | Builder prompt context — directive format, tech rules, phase status |
| `refresh_schema_doc.py` | Regenerates DB_SCHEMA.md from live DuckDB |
| `run_schema.py` | Schema migration runner |
| `quick_seed.py` | Seeds initial MCP data |
| `db_utils.py` | Shared DB helpers (always route via write_service:8772) |
| `http_retry.py` | Shared HTTP retry utilities |
| `config_validator.py` | Config validation utilities |
| `sentinel_cli.py` | CLI entry point |
| `sentinel_sdk.py` | External SDK surface |

### Scanning & Signal Layer (Phase 1–7)

| File | Port | Role |
|------|------|------|
| `mcp_scanner.py` | — | Discovers and scans MCP servers from registry |
| `mcp_data_seeder.py` | — | Seeds/imports MCP server definitions |
| `known_threats.py` | — | Known threat pattern library |
| `signal_analyser.py` | — | Signal analysis daemon (base) |
| `signal_analyser_v2.py` | — | v2 with injection_resilience dimension |
| `mcp_fingerprinter.py` | — | Fingerprints MCP tools for change detection |
| `fingerprint_runner_daemon_v3.py` | — | Fingerprint scheduling daemon |
| `url_analyser.py` | — | Domain/URL trust signals |
| `text_patterns.py` | — | Text pattern matching for descriptions |
| `deduplicator.py` | — | Deduplication logic |
| `similarity_scorer.py` | — | Cross-server similarity scoring |
| `behavioral_analyser.py` | — | Behavioral pattern analysis |
| `anomaly_detector.py` | — | Anomaly detection |
| `cve_enricher.py` | — | CVE enrichment for dependencies |
| `shodan_exposure_correlator.py` | — | Shodan exposure data correlation |
| `github_repo_velocity.py` | — | GitHub commit velocity scorer |
| `npm_typo_squatter.py` | — | npm typosquat detection |
| `manifest_blast_radius.py` | — | Supply chain blast radius calculator |
| `cross_registry_correlator.py` | — | Cross-registry pattern correlation |
| `dependency_chain_auditor.py` | — | Dependency chain audit |
| `supply_chain_enrichment_wiring.py` | — | Supply chain enrichment pipeline wiring |
| `mcp_impersonation_detector.py` | — | MCP server impersonation detection |

### Trust & Verdict Layer

| File | Port | Role |
|------|------|------|
| `trust_synthesiser.py` | — | 7-signal trust score synthesis (base) |
| `trust_synthesiser_v2.py` | — | Phase 8 version with injection_resilience |
| `trust_score_time_series.py` | — | Time-series trust trending |
| `attestation_engine.py` | — | Generates attestation text per verdict |
| `approval_workflow.py` | — | Analyst APPROVED/CONDITIONAL/REJECTED workflow |
| `policy_engine.py` | — | Policy rule enforcement |
| `verdict_explainer.py` | — | Human-readable verdict explanations |
| `risk_ranker.py` | — | Risk ranking across server population |
| `mcp_age_risk_scorer.py` | — | Age-based risk contribution |
| `scoring_cache.py` | — | Score caching layer |
| `stateful_trust_monitor.py` | — | Stateful trust change monitoring |

### Phase 8 — Prompt Injection Harness

| File | Port | Role |
|------|------|------|
| `prompt_injection_scanner.py` | — | Injection pattern scanner |
| `context_manipulation_detector.py` | — | Context manipulation detection |
| `sybil_burst_detector.py` | — | Sybil burst pattern detection |
| `tool_schema_deep_scanner.py` | — | Deep tool schema scanning |
| `pi_corpus_ingest.py` | — | Injection corpus ingestion (quarantine-compliant) |
| `pi_quarantine_reviewer.py` | — | LLM-mediated quarantine triage daemon |
| `pi_quarantine_promoter.py` | — | Mechanical mover to pi_test_corpus |
| `pi_flagged_review_api.py` | `8792` | Review surface API |
| `pi_harness_runner.py` | — | Executes payloads against APPROVED MCPs |
| `pi_scorer.py` | — | injection_resilience signal scorer |

### Phase 9 — Enterprise Integration (In Progress)

| File | Status | Role |
|------|--------|------|
| `snow_connector.py` | Queued | ServiceNow inbound webhook (OAuth, signature validation) |
| `aidr_commit_gateway.py` | Queued | CrowdStrike AI Defense Runtime commit bridge |
| `approval_evidence_bundler.py` | Planned | Signed audit artefact per analyst decision |
| `trust_synthesiser_v3_pi_dimension.py` | Queued | v3 with corrected 7th-dimension weighting |

### HTTP APIs (FastAPI Services)

| File | Port | Role |
|------|------|------|
| `registry_api.py` | 8780 | MCP registry REST API |
| `search_api.py` | 8781 | Search endpoint |
| `dashboard_api.py` | 8782 | Dashboard data API |
| `bulk_assess_api.py` | — | Bulk assessment jobs API |
| `comparison_api.py` | — | Server comparison API |
| `forensic_detail_api.py` | — | Forensic detail endpoint |
| `advanced_filter_api.py` | — | Advanced filter endpoint |
| `manual_override_api.py` | — | Manual override endpoint |
| `pi_flagged_review_api.py` | 8792 | PI review surface |
| `build_watcher_api.py` | 8795 | Build artifact watcher |
| `api_gateway.py` | — | Gateway routing layer |
| `threat_feed_api.py` | — | Threat feed REST endpoint |
| `alert_dispatcher.py` | — | Alert dispatch API |
| `mcp_signal_evidence_api.py` | — | Signal evidence endpoint (recently built) |
| `quarantined_files_api.py` | — | Quarantined files endpoint (recently built) |

### Front-End / HTML

| File | Role |
|------|------|
| `dashboard.html` | Main Sentinel dashboard |
| `sentinel_status.html` | System status page |
| `mcp_submission_portal.html` | MCP submission intake form |
| `mcp_search_results_view.html` | Search results UI (recently built) |
| `ui_server.py` | Serves static HTML |

### Schedulers & Daemons

| File | Role |
|------|------|
| `assessment_scheduler.py` | Schedules reassessments |
| `scan_scheduler.py` | Scan scheduling |
| `stale_data_cleaner.py` | Cleans stale scoring data |
| `rug_pull_monitor.py` | Detects rug-pull patterns |
| `approval_anomaly_detector.py` | Anomaly detection in approval patterns |
| `vendor_concentration_monitor.py` | Vendor concentration risk |
| `runtime_behaviour_profiler.py` | Runtime behaviour profiling |
| `threat_intel_ingestor.py` | Ingests threat intel feeds |
| `threat_correlator.py` | Correlates threat signals |
| `threat_feed_aggregator.py` | Aggregates multiple threat feeds |
| `performance_monitor.py` | System performance monitoring |
| `mcp_profiler.py` | MCP server profiling |
| `backup_service.py` | DB backup service |
| `api_health_checker.py` | Periodic health checks for all APIs |
| `compliance_reporter.py` | Compliance reporting daemon |
| `compliance_export_service.py` | Compliance export |

### Reporting & Analytics

| File | Role |
|------|------|
| `daily_digest.py` | Daily summary email/report |
| `trend_analyser.py` | Trust score trend analysis |
| `compliance_reporter.py` | Compliance reports |
| `report_formatter.py` | Shared report formatting |
| `audit_trail.py` | Audit trail utilities |
| `mesh_sentinel_reporter.py` | Mesh→Sentinel status bridge reporter |

### Utility & Shared

| File | Role |
|------|------|
| `lookup.py` | Fast lookup utilities |
| `watch.py` | File system watcher |
| `rate_limiter.py` | API rate limiting |
| `error_reporter.py` | Centralized error reporting |
| `data_validator.py` | Data validation |
| `false_positive_tracker.py` | FP tracking and suppression |
| `directive_validator.py` | Validates directive JSON before queueing |
| `smoke_evolution_agent.py` | Tracks smoke test pass rates over time |
| `exemption_manager.py` | Manages scan exemptions |
| `github_pr_checker.py` | GitHub PR analysis |
| `npm_webhook_handler.py` | npm registry webhook handler |
| `remediation_advisor.py` | Remediation recommendation engine |
| `certificate_analyser.py` | TLS certificate analysis |
| `pattern_learner.py` | Learns from confirmed verdicts |
| `analyst_feedback_loop.py` | Analyst feedback integration |
| `queue_manager.py` | Generic queue management |
| `metrics_exporter.py` | Prometheus/metrics export |
| `context_injector.py` | Context injection for LLM prompts |
| `mesh_bridge.py` | Sentinel → Mesh bridge |
| `registry_reconciler.py` | Registry sync/reconciliation |
| `notification_hub.py` | Notification dispatch hub |
| `webhook_dispatcher.py` | Outbound webhook dispatcher |
| `incident_webhook_dispatcher.py` | Security incident webhooks |
| `email_guid_auth.py` | Email GUID authentication |
| `integration_test.py` | Integration test suite |

### Builder System Files (in `zo_sentinel/`)

| File | Role |
|------|------|
| `zo_sentinel_builder.py` | **Master builder daemon** — polls directives, generates code, runs smoke tests, escalation ladder |
| `goose_runner.py` | **Goose execution runner** — routes directives to Goose/ladder/fallback |
| `sentinel_directive_generator.py` | **Directive generator** — LLM-driven (MiniMax/Ollama) new directive creation |
| `SENTINEL_DIRECTIVE_SCHEMA.md` | **Builder context** — tech rules, phase status, already-built registry |
| `DB_SCHEMA.md` | **Schema context** — live-generated table reference |
| `tools/sentinel_janitor.sh` | Ghost `.done` sweep + daemon healing |

### Promoters Submodule (`zo_sentinel/promoters/`)

| File | Role |
|------|------|
| `proposed_to_pending_promoter.py` | Promotes `proposed/` directives → `pending/` |
| `candidate_promoter_daemon.py` | MCP candidate promotion |
| `candidate_npm_promoter.py` | npm candidate promotion |
| `candidate_github_promoter.py` | GitHub candidate promotion |
| `registry_promoter_daemon.py` | Registry promotion daemon |

### Directive Directories (`zo_sentinel/` subdirs)

| Directory | Contents |
|-----------|----------|
| `directives/pending/` | JSON directive files awaiting execution |
| `directives/done/` | Completed directive sentinels (`.done` files) |
| `directives/proposed/` | LLM-proposed directives pre-promotion |
| `directives/failed/` | Failed directive records (`.failed.json`) |

---

## ZO Mesh — File Structure

Root: `/home/workspace/zo_mesh/`

### Infrastructure Daemons

| File | Port | Role |
|------|------|------|
| `watchdog.sh` | — | **Master watchdog** — process supervisor, restarts crashed daemons every 900s |
| `watchdog_daemon.py` | — | Python wrapper that schedules `watchdog.sh` |
| `write_service_wrapper.sh` | `8772` | WriteService launcher (DuckDB write gateway) |
| `inference_router_service.py` | `8773` | LLM inference routing (MiniMax → Gemini → Ollama cascade) |
| `daemon_wrapper.sh` | — | Generic daemon restart wrapper with crash logging |

### Mesh Processing Daemons

| File | Role |
|------|------|
| `pipeline_bridge.py` | Bridges mesh events to Sentinel pipeline |
| `t2_consumer_agents.py` | Tier-2 event consumer agents |
| `anti_entropy_daemon.py` | Detects and repairs data inconsistencies |
| `mesh_self_diagnostics.py` | Self-diagnostic health reporting |
| `data_velocity_engine.py` | Tracks and optimizes data throughput |
| `wisdom_synthesiser.py` | Synthesizes cross-agent learnings into mesh_memory |
| `run_manager.py` | Agent run lifecycle manager |

### Trust Pipeline (supervised by watchdog)

These Sentinel daemons are monitored by the watchdog for process health:

```
candidate_promoter_daemon.py
candidate_npm_promoter.py
registry_promoter_daemon.py
fingerprint_runner_daemon_v3.py
mcp_scanner.py
signal_analyser.py
trust_synthesiser.py
threat_intel_ingestor.py
risk_ranker.py
attestation_engine.py
```

### Log Files (in `/home/workspace/logs/`)

| Log File | Source Daemon |
|----------|---------------|
| `zo_sentinel_builder.log` | zo_sentinel_builder.py |
| `sentinel_sentinel_directive_generator.log` | sentinel_directive_generator.py |
| `goose_runner.log` | goose_runner.py |
| `watchdog_daemon.log` | watchdog_daemon.py |
| `watchdog.log` | watchdog.sh |
| `write_service.log` | write_service_wrapper.sh |
| `inference_router.log` | inference_router_service.py |
| `pipeline_bridge.log` | pipeline_bridge.py |
| `signal_bridge.log` | signal bridge daemon |
| `signal_analyser.log` | signal_analyser.py |
| `threat_intel_ingestor.log` | threat_intel_ingestor.py |
| `candidate_promoter_daemon.log` | candidate_promoter_daemon.py |
| `candidate_npm_promoter.log` | candidate_npm_promoter.py |
| `candidate_github_promoter.log` | candidate_github_promoter.py |
| `registry_promoter_daemon.log` | registry_promoter_daemon.py |
| `proposed_to_pending_promoter.log` | proposed_to_pending_promoter.py |
| `sentinel_janitor.log` | tools/sentinel_janitor.sh |
| `world_agent.log` | world_agent/run.py |
| `ollama.log` | Ollama model server |

---

## Builder & Directive Generator System

The builder is an autonomous code-generation loop, not a human-driven build tool.

### Architecture

```
sentinel_directive_generator.py   ← generates directive JSON
    ↓  writes to  directives/proposed/
proposed_to_pending_promoter.py   ← validates and promotes
    ↓  moves to  directives/pending/
goose_runner.py                   ← polls pending/, routes to execution
    ├── Goose AI (recipe-based)
    └── Ladder shim fallback (MiniMax → Gemini → Ollama)
    ↓  output written to  zo_sentinel/<file>.py
zo_sentinel_builder.py            ← alternative builder path (smoke-tested)
    ↓  smoke test → Tier-0 gate (syntax + import check)
    ↓  on pass: marks directive .done
    ↓  on fail: RESCUE cycle (1 retry via ladder)
```

### Directive JSON Schema

```json
{
  "task": "unique_snake_case_name",
  "handler": "generate_file",
  "output_file": "filename.py",
  "complexity": "low|medium|high",
  "phase": "12",
  "priority": 0.85,
  "description": "Specific task with port, table names, function signatures, daemon pattern.",
  "reads": ["dependency.py"],
  "next_directive": {}
}
```

### Escalation Ladder (LLM cascade)

```
Low complexity  → zo-ladder-low   (Gemini 2.5 Flash direct)
Medium          → zo-ladder-medium (Gemini 2.5 Flash direct)
High            → zo-ladder-high  (escalates model tier)
Fallback #1     → MiniMax (rate-limited — HTTP 429 chronic as of 2026-06-26)
Fallback #2     → Ollama (local, slow, ~3 min/directive)
```

**Current issue:** MiniMax hit token plan limit (HTTP 429). Directive generator falls back to Ollama every cycle, which fails JSON parsing ~70% of cycles due to Ollama treating the prompt as a document rather than a code task. This is the primary operational bottleneck right now.

### Build Phase Map

| Phase | Status | Description |
|-------|--------|-------------|
| 1–7 | ✅ Complete | Core scanning, signals, trust pipeline |
| 8 | ✅ Build complete | Prompt injection harness (6 pi_* daemons) |
| 9 | 🔄 Unlocked | Enterprise integration (ServiceNow, AiDr, approval bundler) |
| 10+ | Planned | TBD |

### Key Invariants (NEVER violate)

1. **All DB writes/reads go via WriteService `:8772`** — never `duckdb.connect()` directly
2. **`ON CONFLICT` not `INSERT OR IGNORE`** — DuckDB syntax requirement
3. **`'rows': [...]` not `'row': {...}`** — WriteService API contract
4. **Never auto-commit `CAUTION_LIMITED` or `HIGH_RISK_ISOLATED`** — AiDr gateway safety gate
5. **No `input()` calls in any generated file** — all decisions LLM-mediated + JSONL-logged
6. **Builder must not rewrite itself** (`zo_sentinel_builder.py` = human-edit only)
7. **Ghost guard**: a directive is DONE only when declared `output_file` exists on disk AND passes Tier-0 gate

---

## Storage Layer — ZO Mesh DuckDB

**Location:** `/home/workspace/Datasets/zo-mesh/data.duckdb`  
**Size:** 758.51 MB  
**Access:** WriteService HTTP API at `http://127.0.0.1:8772`

### Table Inventory (42 tables, live row counts)

| Table | Rows | Purpose |
|-------|------|---------|
| `mesh_memory` | 73,289 | Agent memories, watchdog ticks, world state |
| `mesh_events` | 11,661 | Directive lifecycle, build events, signals |
| `inference_log` | 9,966 | LLM call records (model, tokens, latency) |
| `agent_runs` | 2,452 | Agent execution records |
| `agent_outputs` | 2,448 | Agent output payloads |
| `corrections` | 1,257 | Analyst corrections / feedback |

#### MCP Trust Tables

| Table | Purpose |
|-------|---------|
| `mcp_server_registry` | Master MCP server list (name, url, registry_source, trust_score, verdict) |
| `mcp_signal_scores` | Per-signal scores (domain_trust, tool_description_safety, permission_scope, supply_chain, community_signal, temporal_stability, injection_resilience) |
| `mcp_fingerprints` | Tool hash fingerprints for change detection |
| `mcp_attestations` | Generated attestation text per verdict |
| `mcp_decisions` | Analyst APPROVED/CONDITIONAL/REJECTED decisions |
| `mcp_submissions` | Submission intake records |
| `mcp_exemptions` | Active exemptions |
| `mcp_risk_register` | Risk register (computed_at field) |
| `mcp_policy_rules` | Policy rules (rule_type + pattern columns) |
| `mcp_definition_history` | Tool schema snapshots for drift detection |
| `mcp_llm_axis_scores` | LLM axis classification probabilities |
| `mcp_threat_associations` | Threat-to-server associations |
| `mcp_tool_hashes` | Tool-level hash tracking |
| `mcp_signal_enrichments` | Enrichment data per signal |

#### Audit & Compliance Tables

| Table | Purpose |
|-------|---------|
| `audit_log` | Immutable audit trail (timestamp, target_server_id, actor) |
| `auth_tokens` | Email GUID auth tokens |
| `mcp_exemptions` | Exemption records |
| `key_chain_status` | API key health (ladder rung, working path, status) |
| `key_topology` | Key routing topology |

#### Build / Analytics Tables

| Table | Purpose |
|-------|---------|
| `build_provenance` | Build record (directive, model, backend, smoke, output_path) |
| `build_churn_daily` | Daily build churn metrics |
| `build_churn_trend` | 7-day churn trend |
| `failure_matrix` | Per-(directive_type, complexity, model) failure rates |
| `github_velocity` | GitHub repo velocity scores |
| `shodan_results` | Shodan exposure data |
| `npm_typosquat_alerts` | npm typosquat alert log |
| `forensic_cache` | Cached forensic computations |
| `code_nodes` | Code graph nodes (files, functions, classes) |
| `code_edges` | Code graph edges (import, call, inherits relationships) |
| `perf_metrics` | Performance metrics |
| `service_health` | Daemon heartbeats (service, last_heartbeat, status, meta) |
| `write_queue_log` | WriteService queue audit log |
| `threat_intel_articles` | Threat intel article store |
| `world_articles` | World agent article store |
| `world_topics` | World agent topic taxonomy |
| `bulk_assess_jobs` | Bulk assessment job tracking |
| `bulk_imports` | Bulk import records |

### WriteService API Reference

```python
# Write rows (all INSERT paths)
requests.post('http://127.0.0.1:8772/write', json={
    'table': 'table_name',
    'rows': [{'col': 'val', ...}],   # always list, never dict
    'wait': True                      # synchronous confirmation
})

# Query (SELECT)
requests.post('http://127.0.0.1:8772/query', json={
    'sql': 'SELECT ... FROM ...'
})

# Health check
requests.get('http://127.0.0.1:8772/health')  # → 200 OK
```

---

## Supporting Components

### World Agent (`/home/workspace/world_agent/`)

| Item | Description |
|------|-------------|
| `run.py` | Main daemon (`python run.py --daemon`) |
| `memories/world_state` | Current world context |
| `memories/temporal_context` | Time/urgency signals |
| `memories/agent_instructions` | Active agent instructions |
| `memories/topics/` | Per-topic memory files |

**Agents running (from DB):**

| Agent | Run Count | Last Run |
|-------|-----------|----------|
| t1.temporal_context | 1,257 | 2026-06-26 14:41 UTC |
| t1.ai_research_scout | 444 | 2026-06-26 14:02 UTC |
| t1.world_agent | 107 | 2026-06-26 13:23 UTC |
| t1.synthesize | 348 | 2026-06-22 |
| t1.log_guardian | 125 | 2026-06-22 |
| t1.prediction_engine | 49 | 2026-06-22 |
| t1.referral_engine | 32 | 2026-06-22 |

### Skills (`/home/workspace/Skills/`)

| Component | Type | Description |
|-----------|------|-------------|
| `childofintent-intent-engine/` | Mixed Python/TS | Intent engine daemon |
| `childofintent-intent-engine/scripts/intent_engine_daemon.py` | Python | Intent engine daemon |
| Various skill definitions | TypeScript (`.ts`) | Skill definitions — **NOTE: watchdog scans these for `model_name.*byok:` pattern** |

---

## Migration Plan

### Goal

Clone ZO Sentinel + ZO Mesh source code into GitHub, establishing a versioned, off-machine backup with a clean repo layout separating application code from runtime state.

### What to Migrate (Scope)

| Include | Exclude |
|---------|---------|
| All `.py` files in `zo_sentinel/` | `data.duckdb` (758 MB — too large, use export) |
| All `.sh`, `.py` files in `zo_mesh/` | `logs/*.log` (ephemeral runtime state) |
| `SENTINEL_DIRECTIVE_SCHEMA.md` | `directives/done/` (thousands of sentinel files) |
| `DB_SCHEMA.md` | `.bak.*` orphan backups |
| HTML files in `zo_sentinel/` | `shared/outputs/goose/` (generated artifacts) |
| `PROMPT_INJECTION_PLAN.md` and any other `.md` planning docs | Credentials / API keys |
| `directives/pending/*.json` | `directives/failed/*.json` |
| `world_agent/` Python source | `world_agent/memories/` (runtime state) |
| `Skills/` TypeScript & Python source | |

### Recommended Repo Structure (Target)

```
zo-sentinel/                        ← root of GitHub repo
│
├── zo_sentinel/                    ← core Sentinel codebase
│   ├── __init__.py
│   ├── schema.py
│   ├── schema_v2.py
│   ├── db_utils.py
│   ├── http_retry.py
│   ├── ... (all ~130 .py files)
│   ├── promoters/
│   │   ├── proposed_to_pending_promoter.py
│   │   ├── candidate_promoter_daemon.py
│   │   └── ...
│   ├── tools/
│   │   └── sentinel_janitor.sh
│   └── static/
│       ├── dashboard.html
│       ├── sentinel_status.html
│       └── mcp_submission_portal.html
│
├── zo_mesh/                        ← mesh infrastructure
│   ├── watchdog.sh
│   ├── watchdog_daemon.py
│   ├── write_service_wrapper.sh
│   ├── inference_router_service.py
│   ├── daemon_wrapper.sh
│   ├── pipeline_bridge.py
│   ├── t2_consumer_agents.py
│   ├── anti_entropy_daemon.py
│   ├── mesh_self_diagnostics.py
│   ├── data_velocity_engine.py
│   ├── wisdom_synthesiser.py
│   └── run_manager.py
│
├── world_agent/                    ← world agent source
│   └── run.py
│
├── builder/                        ← builder + directive system
│   ├── zo_sentinel_builder.py
│   ├── goose_runner.py
│   ├── sentinel_directive_generator.py
│   ├── SENTINEL_DIRECTIVE_SCHEMA.md
│   └── directives/
│       └── pending/                ← current pending directive queue
│
├── schema/
│   ├── DB_SCHEMA.md                ← live-generated schema reference
│   └── migrations/                 ← any migration scripts
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PROMPT_INJECTION_PLAN.md
│   └── RETROFIT.md
│
├── .gitignore
└── README.md
```

### Step-by-Step Migration

#### Phase 1 — Prepare on ZoComputer (run on ZoComputer via SSH or zo_run_script)

```bash
cd /home/workspace

# Initialize git tracking in workspace
git init zo-sentinel-git
cd zo-sentinel-git

# Copy source files (excludes runtime state)
rsync -av --exclude='*.pyc' --exclude='__pycache__' \
    --exclude='*.log' --exclude='*.duckdb' \
    --exclude='directives/done/' --exclude='directives/failed/' \
    --exclude='.bak.*' \
    /home/workspace/zo_sentinel/ ./zo_sentinel/

rsync -av --exclude='*.pyc' --exclude='__pycache__' \
    /home/workspace/zo_mesh/ ./zo_mesh/

rsync -av --exclude='memories/' \
    /home/workspace/world_agent/ ./world_agent/

# Copy planning docs
mkdir -p builder schema docs
cp /home/workspace/zo_sentinel/zo_sentinel_builder.py ./builder/
cp /home/workspace/zo_sentinel/goose_runner.py ./builder/
cp /home/workspace/zo_sentinel/sentinel_directive_generator.py ./builder/
cp /home/workspace/zo_sentinel/SENTINEL_DIRECTIVE_SCHEMA.md ./builder/
cp /home/workspace/zo_sentinel/DB_SCHEMA.md ./schema/
cp /home/workspace/zo_sentinel/directives/pending/*.json ./builder/directives/pending/ 2>/dev/null || true
```

#### Phase 2 — Connect to GitHub

```bash
cd /home/workspace/zo-sentinel-git

git remote add origin https://github.com/rob531/zo-sentinel.git

# Create .gitignore
cat > .gitignore << 'EOF'
__pycache__/
*.pyc
*.pyo
*.log
*.duckdb
*.duckdb.wal
directives/done/
directives/failed/
*.bak.*
shared/outputs/
memories/
.env
*.key
EOF

git add .
git commit -m "feat: initial ZO Sentinel + ZO Mesh source migration

Migrates all Python source from /home/workspace/zo_sentinel and
/home/workspace/zo_mesh into versioned GitHub repo.

Phase 8 (prompt injection harness) complete.
Phase 9 (enterprise integration) in progress.
42-table DuckDB schema documented in schema/DB_SCHEMA.md."

git push -u origin main
```

#### Phase 3 — DuckDB Schema Export (separate from code)

```bash
# Export schema snapshot only (no data)
cd /home/workspace
python3 zo_sentinel/refresh_schema_doc.py

# Export table structures as SQL
python3 -c "
import duckdb
# Use WriteService query endpoint to avoid lock conflicts
import requests
r = requests.post('http://127.0.0.1:8772/query', json={
    'sql': \"SELECT sql FROM duckdb_tables() WHERE schema_name = 'main'\"
})
print(r.json())
" > schema/create_tables.sql
```

#### Phase 4 — CI / Smoke Test Setup

Add GitHub Actions workflow to validate all Python files on push:

```yaml
# .github/workflows/smoke.yml
name: Smoke Tests
on: [push, pull_request]
jobs:
  syntax-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: |
          find zo_sentinel/ zo_mesh/ -name '*.py' | \
            xargs python3 -m py_compile && echo "All files parse OK"
```

#### Phase 5 — Ongoing Sync

Set up a cron or ZO agent that commits new builder output files:

```bash
# Run nightly or after each build cycle
cd /home/workspace/zo-sentinel-git
git pull origin main
rsync -av --delete --exclude='*.pyc' --exclude='__pycache__' \
    /home/workspace/zo_sentinel/ ./zo_sentinel/
git add -A
git diff --cached --quiet || git commit -m "chore: sync builder output $(date -u +%Y-%m-%d)"
git push origin main
```

---

## Repo Structure (Target GitHub Layout)

```
https://github.com/rob531/zo-sentinel
├── main branch  ← production-stable source
├── dev branch   ← builder output before review
└── Tags:
    v0.8.0       ← Phase 8 complete (current)
    v0.9.0       ← Phase 9 complete (in progress)
```

### Recommended Secrets (GitHub → Settings → Secrets)

| Secret | Purpose |
|--------|---------|
| `MINIMAX_API_KEY` | Directive generator primary LLM |
| `GEMINI_API_KEY` | Ladder escalation |
| `OLLAMA_HOST` | Local Ollama endpoint |
| `ZO_WRITE_SERVICE_URL` | WriteService base URL |

---

*This plan was auto-generated by mapping the live ZO Computer system via MCP tools on 2026-06-26.*
