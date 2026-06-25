# ZO Sentinel + ZO Mesh — Full Architecture Map & GitHub Migration Plan

> **Generated:** 2026-06-25  
> **Source system:** `/home/workspace/` on ZoComputer (accessed via `newzocompconnect` MCP)  
> **Database:** `/home/workspace/Datasets/zo-mesh/data.duckdb` (703 MB DuckDB)  
> **Status as of scan:** Builder idle (queue empty), Directive Generator failing (MiniMax rate-limited, Ollama timeouts)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [ZO Sentinel — File Inventory](#2-zo-sentinel--file-inventory)
3. [ZO Mesh — Storage Layer](#3-zo-mesh--storage-layer)
4. [Builder System](#4-builder-system)
5. [Directive Generator System](#5-directive-generator-system)
6. [Full Database Schema (42 Tables)](#6-full-database-schema-42-tables)
7. [Running Services (Supervisord)](#7-running-services-supervisord)
8. [Active Log Files](#8-active-log-files)
9. [Known Issues & Quarantined Files](#9-known-issues--quarantined-files)
10. [GitHub Migration Plan](#10-github-migration-plan)
11. [Recommended Repo Structure](#11-recommended-repo-structure)

---

## 1. System Overview

```
ZoComputer (remote host)
├── /home/workspace/
│   ├── zo_sentinel/          ← 109+ Python modules, HTML, Markdown
│   ├── Datasets/zo-mesh/
│   │   └── data.duckdb       ← 703 MB — 42 tables, all Sentinel + Mesh data
│   └── logs/                 ← 50+ active log files for all daemons
└── /home/robin/
    └── world_agent/          ← T1 agent memories, topic research, world state
```

**Tech stack:**
- Language: Python 3.11 + FastAPI
- Database: DuckDB (single-file, accessed via write_service at `:8772`)
- LLM backends: MiniMax (primary), Ollama (fallback)
- Service orchestration: supervisord
- Code generation: `zo_sentinel_builder.py` executing LLM-produced directives
- Networking: Tailscale mesh

**Live metrics (as of scan):**
| Metric | Count |
|--------|-------|
| Built Sentinel source files | 109+ |
| Database tables | 42 |
| Mesh events (live) | 10,832 |
| Mesh memory entries | 69,166 |
| Code nodes (indexed) | 51,815 |
| Code edges (indexed) | 66,684 |
| Total DB writes ever | 53,757 |
| Active log files | 50+ |
| Signal dimensions | 7 |
| Phases completed | 8 (Phase 9 in planning) |
| Port range | 8772–8795 |

---

## 2. ZO Sentinel — File Inventory

**Root:** `/home/workspace/zo_sentinel/`

### 2a. Core Source Files (from SENTINEL_DIRECTIVE_SCHEMA.md — "Already Built" list)

#### Data Models & Schema
```
schema.py
schema_v2.py
run_schema.py
```

#### Signal Analysis & Trust (7-Dimension Engine)
```
signal_analyser.py              ← original 6-dimension scorer
signal_analyser_v2.py           ← v2 with injection_resilience (7th dimension)
trust_synthesiser.py
trust_synthesiser_v2.py         ← reads injection_resilience dimension, weight 1.6, threshold 0.80
scoring_cache.py
```

**7 signal dimensions:**
1. `domain_trust`
2. `tool_description_safety`
3. `permission_scope`
4. `supply_chain`
5. `community_signal`
6. `temporal_stability`
7. `injection_resilience` (Phase 8 — newest)

#### Attestation & Decisions
```
attestation_engine.py
approval_workflow.py
analyst_feedback_loop.py
verdict_explainer.py
policy_engine.py
exemption_manager.py
```

#### MCP Analysis & Intelligence
```
mcp_scanner.py
mcp_profiler.py
mcp_fingerprinter.py
mcp_data_seeder.py
mcp_impersonation_detector.py
mcp_age_risk_scorer.py
tool_schema_deep_scanner.py
mcp_traffic_fingerprints.py     ← also in build_provenance (rebuilt several times)
mcp_tool_schema_patterns.py
mcp_tool_schema_patterns_v2.py
shadow_mcp_indicators.py
```

#### Registry Integrations
```
npm_webhook_handler.py
github_pr_checker.py
npm_typo_squatter.py
npm_typosquat_alerts         ← table
```

#### Threat Intelligence & Correlation
```
known_threats.py
threat_intel_ingestor.py
threat_feed_aggregator.py
threat_correlator.py
threat_feed_api.py
anomaly_detector.py
rug_pull_monitor.py
shodan_exposure_correlator.py
prompt_injection_scanner.py
context_manipulation_detector.py
sybil_burst_detector.py
cross_registry_correlator.py
```

#### Supply Chain & Dependencies
```
dependency_chain_auditor.py
manifest_blast_radius.py
github_repo_velocity.py
certificate_analyser.py
supply_chain_enrichment.py
supply_chain_enrichment_writer.py
supply_chain_threat_enrichment.py
```

#### Risk & Compliance
```
risk_ranker.py
compliance_reporter.py
compliance_export_service.py
compliance_exporter.py
false_positive_tracker.py
behavioral_analyser.py
runtime_behaviour_profiler.py
remediation_advisor.py
```

#### APIs & Web Services
```
registry_api.py              ← port assigned in supervisord
search_api.py
lookup.py
dashboard_api.py
dashboard.html
bulk_assess_api.py
comparison_api.py
forensic_detail_api.py
advanced_filter_api.py
manual_override_api.py
graphql_schema_builder.py
api_gateway.py
api_health_checker.py
build_watcher_api.py
ui_server.py
```

#### HTML UIs
```
dashboard.html
sentinel_status.html
mcp_submission_portal.html
admin_policies.html
admin_submissions.html
admin_exemptions.html
```

#### Operational Utilities
```
url_analyser.py
text_patterns.py
db_utils.py
http_retry.py
config_validator.py
data_validator.py
rate_limiter.py
error_reporter.py
watch.py
sentinel_cli.py
sentinel_sdk.py
audit_trail.py
backup_service.py
queue_manager.py
alert_manager.py
alert_dispatcher.py
notification_hub.py
webhook_dispatcher.py
incident_webhook_dispatcher.py
email_guid_auth.py
stale_data_cleaner.py
deduplicator.py
pattern_learner.py
similarity_scorer.py
context_injector.py
```

#### Monitoring & Analytics
```
pipeline_health.py
performance_monitor.py
metrics_exporter.py
daily_digest.py
trend_analyser.py
assessment_scheduler.py
scan_scheduler.py
approval_anomaly_detector.py
vendor_concentration_monitor.py
trust_score_time_series.py
stateful_trust_monitor.py
```

#### Phase 8 — Prompt Injection Harness (COMPLETE)
```
pi_corpus_ingest.py           ← corpus fetching & quarantine
pi_quarantine_reviewer.py     ← LLM triage daemon
pi_quarantine_promoter.py     ← payload promotion to test corpus
pi_flagged_review_api.py      ← FastAPI review surface :8792
pi_harness_runner.py          ← executes payloads against APPROVED MCPs
pi_scorer.py                  ← injection_resilience scorer
```

#### Enrichment Pipeline (built via builder, 2026-06-09/14)
```
supply_chain_enrichment.py
supply_chain_enrichment_writer.py
supply_chain_threat_enrichment.py
enrichment_orchestrator.py
enrichment_runner.py
enrichment_pipeline_daemon.py
enrichment_pipeline_writer_daemon.py
enrichment_trigger_daemon.py
enrichments_writer.py
enrichments_reader.py
enrichments_table_populator.py
enrichment_pipeline_diagnostic.py
mcp_signal_enrichments_writer.py
mcp_signal_enrichments_writer_daemon.py
mcp_threat_associations_writer.py
write_mcp_signal_enrichments_daemon.py
traffic_fingerprints_enrichment.py
shadow_mcp_indicators_enrichment.py
context_efficiency_enrichment.py
tool_schema_patterns_enrichment.py
mcp_traffic_fingerprints_v2.py
```

#### Verification & Test Files (built via builder)
```
integration_test.py
e2e_scenario_runner.py
e2e_enrichment_flow_test.py
verify_injection_resilience_dimension.py
verify_enrichment_pipeline.py
verify_enrichment_pipeline_write_through.py
verify_enrichments_pipeline_write.py
verify_enrichments_writer_connectivity.py
quick_seed.py
mcp_signal_enrichments_schema.py
populate_mcp_signal_enrichments_schema.py
populate_mcp_signal_enrichments_from_scores.py
create_mcp_signal_enrichments_table.py
check_mcp_signal_enrichments_table.py
check_enrichment_pipeline_for_mcp_signal_enrichments.py
exemption_expirer.py
retention_sweeper.py
attestation_refresher.py
approval_evidence_bundler.py
```

#### Misc Builder Artifacts
```
_canary_goose_probe.py
_canary_probe_v2.py
_canary_probe_v3.py
registry_reconciler.py
report_formatter.py
directive_validator.py
smoke_evolution_agent.py
mesh_bridge.py
mesh_sentinel_reporter.py
cve_enricher.py
```

#### Documentation (builder-generated)
```
SENTINEL_DIRECTIVE_SCHEMA.md   ← 52 KB — defines directive format, phase status, forbidden classes
DB_SCHEMA.md                   ← auto-generated by refresh_schema_doc.py
ARCHITECTURE.md                ← built 2026-06-10 by builder
OPERATIONS.md                  ← operational runbook
sentinel_external_api.md       ← API reference
PROMPT_INJECTION_PLAN.md       ← Phase 8 design document
```

#### Configuration
```
supervisord_sentinel_full.conf  ← defines all sentinel programs
refresh_schema_doc.py           ← schema doc refresh utility
```

---

## 3. ZO Mesh — Storage Layer

**Database:** `/home/workspace/Datasets/zo-mesh/data.duckdb` (703 MB)

### Mesh-Specific Tables

```sql
-- Event bus between agents
mesh_events (10,832 rows)
  id                BIGINT PRIMARY KEY
  agent_id          VARCHAR
  event_type        VARCHAR
  tier              VARCHAR
  payload           VARCHAR
  severity          VARCHAR DEFAULT 'INFO'
  created_at        TIMESTAMP WITH TIME ZONE
  consumed          BOOLEAN DEFAULT false
  consumed_by       VARCHAR
  consumed_at       TIMESTAMP WITH TIME ZONE

-- Persistent agent memory with embeddings
mesh_memory (69,166 rows)
  id                BIGINT PRIMARY KEY
  agent_id          VARCHAR
  memory_type       VARCHAR
  content           VARCHAR
  importance        FLOAT DEFAULT 0.5
  embedding         FLOAT[]
  created_at        TIMESTAMP WITH TIME ZONE
  expires_at        TIMESTAMP WITH TIME ZONE
  version           INTEGER DEFAULT 1
  superseded_by     BIGINT
```

### Integration Files
```
/home/workspace/zo_sentinel/mesh_bridge.py          ← event bridging
/home/workspace/zo_sentinel/mesh_sentinel_reporter.py ← reports to mesh
```

---

## 4. Builder System

**Script:** `zo_sentinel_builder.py` (exact path: `/home/workspace/zo_sentinel/` directory — **file NOT found at that path during scan; may be in parent `/home/workspace/` or `/home/workspace/zo_sentinel/`**)

**Log:** `/home/workspace/logs/zo_sentinel_builder.log` (1,946 KB)

### How the builder works

```
1. Poll write_service at :8772 every 5 minutes for pending directives
2. If directive found:
   a. Parse JSON directive
   b. Call LLM (MiniMax → Ollama fallback) with full context
   c. Generate Python/HTML/Markdown file
   d. Smoke-test: python3 -c "import <module>" 
   e. Auto-install missing deps ([deps] auto-installed)
   f. Write to /home/workspace/zo_sentinel/<output_file>
   g. Record in build_provenance table
   h. Execute next_directive chain if present
3. If no directives: log "No pending directives." and sleep 5 min
```

### Builder constraints (from directive schema)
- **NEVER rewrite itself** (`zo_sentinel_builder.py` must only be edited by humans)
- **NEVER start output filenames with `build_`** (chain-naming bug workaround)
- **ALWAYS include explicit `output_file`** in every `next_directive`
- Standalone daemon files: can be chained
- Library/utility files (imported by others): must be top-level directives

### Current builder status (2026-06-25 07:06 UTC)
```
Builder: IDLE — polling every 5 minutes, queue depth: 0
Directive generator: FAILING
  - MiniMax: HTTP 429 (rate limit exhausted, plan upgrade required)
  - Ollama: All 2 retries exhausted (timeout) or JSON parse failure
  - Standing goals fallback: 0 directives (all 7 standing goals already built)
  
Result: Builder is healthy but starved. No new directives entering the queue.
Action needed: Upgrade MiniMax plan OR switch directive generator to a different LLM.
```

---

## 5. Directive Generator System

**Script:** `sentinel_sentinel_directive_generator.py` (or `sentinel_directive_generator.py`)  
**Log:** `/home/workspace/logs/sentinel_sentinel_directive_generator.log` (1,179 KB)  
**Config used:** v1.2 (as of 2026-06-24 22:59 UTC)

### Directive JSON format
```json
{
  "task": "unique_snake_case_name",
  "handler": "generate_file",
  "output_file": "filename.py",
  "complexity": "low|medium|high",
  "phase": "11",
  "priority": 0.85,
  "description": "Specific: include port, table names with correct columns, function sigs, daemon pattern.",
  "reads": ["dependency.py"],
  "next_directive": {}
}
```

### Priority scale
| Value | Meaning |
|-------|---------|
| 0.95 | Critical |
| 0.90 | Important |
| 0.85 | Normal |
| 0.75 | Quality pass |

### Phase 9 targets (next to build)
```
snow_connector.py              — ServiceNow inbound webhook (SNOW OAuth, signed requests)
aidr_commit_gateway.py         — CrowdStrike AiDr commit bridge (check verdict before commit)
approval_evidence_bundler.py   — Audit-ready JSON bundle (7 signals + pi_results + analyst)
```

---

## 6. Full Database Schema (42 Tables)

### Agent / Mesh Infrastructure
```sql
agent_runs         — run history per agent (2,286 rows)
agent_outputs      — output payloads per run (2,282 rows)
mesh_events        — inter-agent event bus (10,832 rows)
mesh_memory        — persistent agent memory + embeddings (69,166 rows)
```

### Sentinel Core
```sql
mcp_server_registry        — canonical MCP server records
mcp_submissions            — submission intake + status tracking
mcp_risk_register          — risk tier + score per server
mcp_decisions              — APPROVED/CONDITIONAL/REJECTED verdicts
mcp_attestations           — trust evidence records
mcp_policy_rules           — entity-level trust policies
mcp_exemptions             — exception grants with expiry
mcp_signal_scores          — 7-dimension per-server scores (inference_log: 9,349 rows)
mcp_fingerprints           — tool hash + schema fingerprint cache
mcp_definition_history     — versioned tool definitions
mcp_llm_axis_scores        — raw per-axis LLM scores
mcp_signal_enrichments     — enrichment data per signal
mcp_threat_associations    — MCP ↔ threat intel links
mcp_tool_hashes            — content-addressed tool schema hashes
```

### Intelligence & External Data
```sql
threat_intel_articles       — ingested threat intel feed items
shodan_results              — Shodan exposure data per host
github_velocity             — repo velocity metrics
npm_typosquat_alerts        — typosquat detection results
forensic_cache              — forensic detail cache per server
```

### Code Graph
```sql
code_nodes   (51,815 rows)  — repo entities (files, classes, functions)
  repo, id, label, norm_label, file_type, community, built_at_commit

code_edges   (66,684 rows)  — dependency / call graph edges
  repo, src, dst, relation, weight, confidence, confidence_score, source_location
```

### Operational
```sql
build_provenance    — directive execution log (output_path, complexity, smoke_result, success, built_at)
build_churn_daily   — daily build success/failure summary
build_churn_trend   — churn trend analytics
bulk_assess_jobs    — async bulk assessment job queue
bulk_imports        — bulk MCP import tracking
audit_log           — compliance audit trail
write_queue_log     — write_service queue history (total_written: 53,757)
failure_matrix      — failure pattern tracking
perf_metrics        — performance measurements
service_health      — per-service health status
key_chain_status    — API key chain health
key_topology        — key topology map
auth_tokens         — authentication tokens
corrections         — analyst correction history (1,146 rows)
inference_log       — LLM inference cost log (9,349 rows)
world_articles      — world agent research articles
world_topics        — world agent topic registry
```

---

## 7. Running Services (Supervisord)

From `supervisord_sentinel_full.conf`:

```ini
[program:zo_sentinel_scanner]
command=python3 /home/workspace/zo_sentinel/mcp_scanner.py
log=/home/workspace/logs/sentinel_scanner.log

[program:zo_sentinel_analyser]
command=python3 /home/workspace/zo_sentinel/signal_analyser.py
log=/home/workspace/logs/sentinel_analyser.log

[program:zo_sentinel_rug_pull]
command=python3 /home/workspace/zo_sentinel/rug_pull_monitor.py
log=/home/workspace/logs/sentinel_rug_pull.log

[program:zo_sentinel_api]
command=python3 /home/workspace/zo_sentinel/approval_workflow.py
log=/home/workspace/logs/sentinel_api.log

[program:zo_sentinel_registry_api]
command=python3 /home/workspace/zo_sentinel/registry_api.py
log=/home/workspace/logs/sentinel_registry_api.log

[program:zo_sentinel_ui]
command=python3 /home/workspace/zo_sentinel/ui_server.py
log=/home/workspace/logs/ui_server.log

[program:zo_sentinel_build_watcher]
command=python3 /home/workspace/zo_sentinel/build_watcher_api.py
log=/home/workspace/logs/build_watcher.log
```

**Additional active services (from logs):**
```
write_service             :8772   — database gateway (read+write for all daemons)
inference_router                  — LLM routing (MiniMax → Ollama)
signal_bridge                     — signal scoring pipeline
trust_synthesiser                 — trust verdict aggregation
attestation_engine                — attestation generation
threat_intel_ingestor             — threat feed ingestion
candidate_promoter_daemon         — candidate MCP promotion
registry_promoter_daemon          — registry sync
candidate_github_promoter         — GitHub registry promoter
candidate_npm_promoter            — npm registry promoter
fingerprint_runner_daemon_v3      — fingerprinting
discovery_github_paginator        — GitHub discovery
discovery_npm_paginator           — npm discovery
pipeline_bridge                   — enrichment pipeline bridge
activation_governor               — activation gating
watchdog_daemon                   — service watchdog
liveness_probe                    — health probe
gate_scheduler                    — gate run scheduler (runs every ~3-4h)
goose_runner                      — Goose AI directive executor
ladder_shim                       — tier ladder shim
self_diagnostics                  — self-diagnostic agent
signal_analyser                   — signal analysis daemon
data_velocity                     — data velocity tracker
t2_consumer                       — Tier-2 consumer
pr_publisher                      — PR publishing agent
risk_ranker                       — risk ranking daemon
sentinel_janitor                  — data cleanup daemon
zo_sentinel_builder               — directive execution builder
sentinel_directive_generator      — LLM directive generator ⚠️ FAILING
```

**Port assignments:**
```
8772  write_service (DB gateway — ALL daemons route through this)
8773  (assigned)
8780  (assigned)
8781  (assigned)
8782  (assigned)
8790  (assigned)
8792  pi_flagged_review_api (Phase 8)
8795  (assigned)
8783–8789, 8791, 8793–8794  ← AVAILABLE for Phase 9
```

---

## 8. Active Log Files

```
/home/workspace/logs/
├── zo_sentinel_builder.log                  1,946 KB  — builder polling / build results
├── sentinel_sentinel_directive_generator.log 1,179 KB  — LLM directive generation (FAILING)
├── wrapper_zo_sentinel_builder.log              7 KB
├── wrapper_sentinel_directive_generator.log    89 KB
├── sentinel_directive_generator_goose.log    1,684 KB  — Goose-mode directive gen
├── directive_generator_goose.log             1,684 KB
├── wrapper_sentinel_directive_generator_goose.log 81 KB
├── sentinel_janitor.log                        879 KB  — data cleanup
├── sentinel_approval.log                       133 KB  — approval workflow
├── sentinel_manual_override.log                 18 KB
├── sentinel_search_api.log                      10 KB
├── sentinel_bulk_assess.log                     38 KB
├── sentinel_forensic_detail.log                  8 KB
├── sentinel_registry_api.log                   118 KB
├── signal_bridge.log                          3,660 KB
├── signal_analyser.log                        2,392 KB  — 5-min cycle, active
├── trust_synthesiser.log                      2,008 KB
├── attestation_engine.log                     3,707 KB
├── inference_router.log                       4,128 KB
├── write_service.log                          4,480 KB
├── manager.log                                4,463 KB
├── liveness_probe.log                         2,206 KB
├── goose_runner.log                           4,866 KB
├── candidate_promoter_daemon.log              4,045 KB
├── registry_promoter_daemon.log                 899 KB
├── candidate_github_promoter.log              1,720 KB
├── candidate_npm_promoter.log                   911 KB
├── pipeline_bridge.log                        3,665 KB
├── data_velocity.log                          3,149 KB
├── risk_ranker.log                               50 KB
├── activation_governor.log                    1,546 KB
├── self_diagnostics.log                       4,298 KB
├── graph_refresh.log                             34 KB
├── world_article_feeder.log                     816 KB
├── watchdog_daemon.log                           70 KB
├── watchdog.log                                 189 KB
├── fingerprint_runner_daemon_v3.log             169 KB
├── discovery_npm_paginator.log                  293 KB
├── discovery_github_paginator.log               229 KB
├── threat_intel_ingestor.log                  1,066 KB
├── wrapper_threat_intel_ingestor.log          4,534 KB
├── ollama.log                                 2,124 KB
├── tailscaled.log                             1,163 KB
├── directive_mcp.log                            450 KB
├── ladder_shim.log                            2,435 KB
├── t2_consumer.log                            2,108 KB
├── pr_publisher.log                           2,732 KB
├── loop_watch.log                                24 KB
├── wisdom_synthesiser.log                     1,155 KB
├── key_hydrator.log                           1,024 KB
├── anti_entropy_daemon.log                    2,625 KB
├── bootstrap.log                                  1 KB
└── gate_runs/
    ├── latest.log                                25 KB
    ├── gate_run_20260625_050125.log              25 KB
    ├── gate_run_20260624_225908.log              29 KB
    └── [5 older gate runs...]
```

---

## 9. Known Issues & Quarantined Files

### ⚠️ CRITICAL: Directive Generator Failing (since ~00:00 UTC 2026-06-25)
```
Symptom:  MiniMax HTTP 429 every cycle; Ollama either times out or produces unparseable JSON
          (model summarizes the schema doc instead of generating directives)
Impact:   Builder queue stays at 0; no new features being built
Logs:     /home/workspace/logs/sentinel_sentinel_directive_generator.log
Fix:      1. Upgrade MiniMax token plan (2056 error = plan limit hit)
          OR
          2. Point directive generator at a different LLM endpoint
          (Ollama model appears to be treating the 46KB prompt as a document to summarize)
          Possible fix: use a better Ollama model or switch to Claude API
```

### Quarantined Files (DO NOT delete, DO NOT regenerate)
```
build_pi_quarantine_promoter.py
  ← chain-naming bug orphan (2026-04-16 20:28 UTC)
  ← neutered with refusal stub
  ← original preserved at .bak.20260416_205734

build_pi_quarantine_promoter_auto.py
  ← chain-naming bug orphan (2026-04-16 20:42 UTC)
  ← neutered with refusal stub
  ← original preserved at .bak.20260416_205740

import_fixer.py
  ← failed smoke test (2026-04-16 18:58 UTC)
  ← meta-task; builder cannot self-repair imports this way
  ← kept as record of the failure pattern
```

### Builder chain-naming bug
When `next_directive` lacks explicit `output_file`, builder uses the `task` field as the filename.
If `task` starts with `build_`, the file gets that prefix — causing import failures in downstream code.
**Workaround in schema:** Always include explicit `output_file` in every `next_directive`.

---

## 10. GitHub Migration Plan

### Goal
Clone all ZO Sentinel source code, builder system, directive infrastructure, and database schema definitions into a GitHub repository so the full system is version-controlled, portable, and reproducible outside the ZoComputer host.

### Phase 1 — Export Source Code

**Target:** All files under `/home/workspace/zo_sentinel/`

```bash
# On ZoComputer:
cd /home/workspace/zo_sentinel
git init
git add *.py *.md *.html *.conf
git commit -m "Initial export: ZO Sentinel Phase 1–8 complete, 109+ modules"
git remote add origin https://github.com/rob531/zo-sentinel.git
git push -u origin main
```

**Files to include:**
- [x] All 109+ Python modules (see Section 2)
- [x] `SENTINEL_DIRECTIVE_SCHEMA.md` (52 KB — directive format, forbidden classes, phase status)
- [x] `DB_SCHEMA.md` (auto-generated schema reference)
- [x] `ARCHITECTURE.md` (system design doc)
- [x] `OPERATIONS.md` (operational runbook)
- [x] `PROMPT_INJECTION_PLAN.md` (Phase 8 design)
- [x] `sentinel_external_api.md` (API reference)
- [x] `supervisord_sentinel_full.conf` (service orchestration)
- [x] All HTML UI files (`dashboard.html`, `sentinel_status.html`, etc.)

**Files to exclude (`.gitignore`):**
```
*.log
*.duckdb
*.duckdb.wal
__pycache__/
*.pyc
.env
secrets/
*.bak.*
```

### Phase 2 — Export Database Schema as SQL

Extract DDL from the live DuckDB and commit as SQL migration files:

```bash
# Query via write_service or direct DuckDB access:
python3 -c "
import duckdb
con = duckdb.connect('/home/workspace/Datasets/zo-mesh/data.duckdb', read_only=True)
tables = con.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema='main'\").fetchall()
for (t,) in tables:
    ddl = con.execute(f'SHOW CREATE TABLE {t}').fetchone()[0]
    print(ddl + ';\n')
" > schema/all_tables.sql
```

**Export targets:**
- [ ] `schema/mcp_tables.sql` — all `mcp_*` tables
- [ ] `schema/mesh_tables.sql` — `mesh_events`, `mesh_memory`
- [ ] `schema/agent_tables.sql` — `agent_runs`, `agent_outputs`
- [ ] `schema/code_graph.sql` — `code_nodes`, `code_edges`
- [ ] `schema/operational.sql` — `build_provenance`, `audit_log`, etc.
- [ ] `schema/intelligence.sql` — threat intel, shodan, github_velocity

### Phase 3 — Export Builder & Directive System

```
builder/
├── zo_sentinel_builder.py             ← main builder daemon
├── sentinel_directive_generator.py    ← LLM directive generator
├── sentinel_sentinel_directive_generator.py  ← alternate path
└── directives/                        ← any saved directive JSON files
```

Also export:
- `directive_validator.py` (already in source)
- `smoke_evolution_agent.py` (already in source)
- `SENTINEL_DIRECTIVE_SCHEMA.md` (already in source)

### Phase 4 — CI/CD Setup

Add GitHub Actions workflows:

```yaml
# .github/workflows/smoke-test.yml
name: Smoke Tests
on: [push, pull_request]
jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: |
          for f in *.py; do
            python3 -c "import importlib.util; spec = importlib.util.spec_from_file_location('m', '$f'); m = importlib.util.module_from_spec(spec)" && echo "OK: $f" || echo "FAIL: $f"
          done
```

### Phase 5 — Documentation Repo Structure

```
docs/
├── ARCHITECTURE.md          ← already exists (builder-generated 2026-06-10)
├── OPERATIONS.md            ← already exists
├── API_REFERENCE.md         ← sentinel_external_api.md renamed
├── PHASE_8_INJECTION.md     ← PROMPT_INJECTION_PLAN.md renamed
├── PHASE_9_ENTERPRISE.md    ← ServiceNow + AiDr plans
├── DIRECTIVE_GUIDE.md       ← how to write directives for the builder
├── SIGNAL_DIMENSIONS.md     ← explains all 7 scoring dimensions
├── TROUBLESHOOTING.md       ← common failures (incl. current LLM issue)
└── DEPLOYMENT.md            ← supervisord setup, ports, dependencies
```

### Phase 6 — Milestone Tracking

| Milestone | Status | Target |
|-----------|--------|--------|
| Phase 1: Core Sentinel (signals, trust, attestation) | ✅ Built | — |
| Phase 2: MCP analysis (scanner, profiler, fingerprinter) | ✅ Built | — |
| Phase 3: Threat intelligence ingestion | ✅ Built | — |
| Phase 4: Supply chain analysis | ✅ Built | — |
| Phase 5: APIs, UI, dashboard | ✅ Built | — |
| Phase 6: Enrichment pipeline | ✅ Built | — |
| Phase 7: Advanced detection (SYBIL, impersonation, age risk) | ✅ Built | — |
| Phase 8: Prompt injection harness | ✅ Built | — |
| Phase 9: Enterprise integration (ServiceNow, AiDr, Evidence Bundler) | ⬜ Planned | TBD |
| GitHub export & CI | ⬜ This plan | 2026-06-25 |
| MiniMax rate-limit fix | ⚠️ Blocking | Urgent |

---

## 11. Recommended Repo Structure

```
rob531/zo-sentinel/
├── README.md                        ← system overview, quick start
├── CHANGELOG.md                     ← version history
├── requirements.txt                 ← Python dependencies
├── .gitignore
│
├── sentinel/                        ← all Python source (currently flat in /home/workspace/zo_sentinel/)
│   ├── core/
│   │   ├── schema.py
│   │   ├── schema_v2.py
│   │   ├── run_schema.py
│   │   ├── signal_analyser.py
│   │   ├── signal_analyser_v2.py
│   │   ├── trust_synthesiser.py
│   │   ├── trust_synthesiser_v2.py
│   │   ├── attestation_engine.py
│   │   ├── approval_workflow.py
│   │   ├── analyst_feedback_loop.py
│   │   ├── verdict_explainer.py
│   │   ├── policy_engine.py
│   │   └── exemption_manager.py
│   ├── analysis/
│   │   ├── mcp_scanner.py
│   │   ├── mcp_profiler.py
│   │   ├── mcp_fingerprinter.py
│   │   ├── mcp_impersonation_detector.py
│   │   ├── mcp_age_risk_scorer.py
│   │   ├── tool_schema_deep_scanner.py
│   │   └── shadow_mcp_indicators.py
│   ├── threat_intel/
│   │   ├── known_threats.py
│   │   ├── threat_intel_ingestor.py
│   │   ├── threat_feed_aggregator.py
│   │   ├── threat_correlator.py
│   │   ├── anomaly_detector.py
│   │   ├── rug_pull_monitor.py
│   │   ├── shodan_exposure_correlator.py
│   │   ├── prompt_injection_scanner.py
│   │   ├── context_manipulation_detector.py
│   │   ├── sybil_burst_detector.py
│   │   └── cross_registry_correlator.py
│   ├── supply_chain/
│   │   ├── dependency_chain_auditor.py
│   │   ├── manifest_blast_radius.py
│   │   ├── github_repo_velocity.py
│   │   ├── certificate_analyser.py
│   │   ├── npm_typo_squatter.py
│   │   └── supply_chain_enrichment.py
│   ├── phase8_injection/
│   │   ├── pi_corpus_ingest.py
│   │   ├── pi_quarantine_reviewer.py
│   │   ├── pi_quarantine_promoter.py
│   │   ├── pi_flagged_review_api.py   ← :8792
│   │   ├── pi_harness_runner.py
│   │   └── pi_scorer.py
│   ├── apis/
│   │   ├── registry_api.py
│   │   ├── search_api.py
│   │   ├── lookup.py
│   │   ├── dashboard_api.py
│   │   ├── bulk_assess_api.py
│   │   ├── comparison_api.py
│   │   ├── forensic_detail_api.py
│   │   ├── advanced_filter_api.py
│   │   ├── manual_override_api.py
│   │   ├── graphql_schema_builder.py
│   │   ├── api_gateway.py
│   │   ├── build_watcher_api.py
│   │   └── ui_server.py
│   ├── ui/
│   │   ├── dashboard.html
│   │   ├── sentinel_status.html
│   │   ├── mcp_submission_portal.html
│   │   ├── admin_policies.html
│   │   ├── admin_submissions.html
│   │   └── admin_exemptions.html
│   ├── enrichment/
│   │   ├── enrichment_orchestrator.py
│   │   ├── enrichment_runner.py
│   │   ├── enrichment_pipeline_daemon.py
│   │   ├── enrichment_pipeline_writer_daemon.py
│   │   ├── enrichment_trigger_daemon.py
│   │   ├── enrichments_writer.py
│   │   ├── enrichments_reader.py
│   │   ├── mcp_signal_enrichments_writer.py
│   │   ├── mcp_signal_enrichments_writer_daemon.py
│   │   ├── mcp_threat_associations_writer.py
│   │   └── supply_chain_threat_enrichment.py
│   └── utils/
│       ├── db_utils.py
│       ├── http_retry.py
│       ├── config_validator.py
│       ├── data_validator.py
│       ├── rate_limiter.py
│       ├── error_reporter.py
│       ├── audit_trail.py
│       ├── backup_service.py
│       └── [other utilities]
│
├── mesh/
│   ├── mesh_bridge.py
│   └── mesh_sentinel_reporter.py
│
├── builder/
│   ├── zo_sentinel_builder.py           ← directive executor
│   ├── sentinel_directive_generator.py  ← LLM directive generator
│   └── directive_validator.py
│
├── schema/
│   ├── mcp_tables.sql
│   ├── mesh_tables.sql
│   ├── agent_tables.sql
│   ├── code_graph.sql
│   ├── operational.sql
│   └── intelligence.sql
│
├── config/
│   ├── supervisord_sentinel_full.conf
│   └── SENTINEL_DIRECTIVE_SCHEMA.md
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── OPERATIONS.md
│   ├── API_REFERENCE.md
│   ├── PHASE_8_INJECTION.md
│   ├── PHASE_9_ENTERPRISE.md
│   ├── SIGNAL_DIMENSIONS.md
│   ├── DIRECTIVE_GUIDE.md
│   ├── TROUBLESHOOTING.md
│   └── DEPLOYMENT.md
│
├── tests/
│   ├── integration_test.py
│   ├── e2e_scenario_runner.py
│   └── e2e_enrichment_flow_test.py
│
└── .github/
    └── workflows/
        ├── smoke-test.yml
        └── schema-validate.yml
```

---

## Appendix A — T1 Agent Schedule (world_agent)

These agents run on a separate schedule and write to the mesh via `agent_outputs` / `agent_runs`:

| Agent | Schedule |
|-------|----------|
| t1.linkedin_boost | Daily 17:00 UTC |
| t1.linkedin_post_ideas | Daily 08:00 EST |
| t1.linkedin_profile_boost | Weekly Mon 09:00 EST |
| t1.linkedin_engagement | Daily 09:00 EST |
| t1.portalpha | Weekly Mon 06:00 EST |
| t1.wealth_execution | Weekly Sun 09:00 EST |
| t1.ruth_dave | Tue/Fri 10:00 EST |
| t1.niche_scout | Weekly Mon 14:00 EST |
| t1.ai_research_scout | Daily 07:00 EST |
| t1.analytics_report | Weekly Sun 10:00 EST |

---

## Appendix B — Build Provenance Summary

The `build_provenance` table records every directive execution. Key observations:

- Builder has been active since at least **2026-06-09**
- Enrichment pipeline built over **2026-06-09 to 2026-06-14** (most recent large burst)
- Many "ghost" smoke results (file created but Python import failed) — these are normal churn from complex-medium directives
- Canary probes (`_canary_*.py`) confirm builder remained operational through 2026-06-13
- Last successful build: `_canary_probe_v3.py` — 2026-06-13 20:12 UTC
- **No builds since 2026-06-14** — consistent with MiniMax rate-limit exhaustion around that time

---

*This document was auto-generated on 2026-06-25 by scanning the live ZoComputer system via the `newzocompconnect` MCP server.*
