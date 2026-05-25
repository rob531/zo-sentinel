# ZO-SENTINEL Directive Schema & Context
# For: sentinel_directive_generator.py
# Updated: 2026-04-16 (Phase 8 layer 1+2 complete, Phase 9 unlocked)
#
# TABLE/COLUMN REFERENCE: See DB_SCHEMA.md (auto-generated from live DB)
# Regenerate schema: python3 /home/workspace/zo_sentinel/refresh_schema_doc.py

---

## What Is ZO-SENTINEL?

MCP server trust intelligence for enterprise InfoSec. Workflow:
1. Developer submits MCP server for InfoSec review
2. ZO-SENTINEL scores across 7 signals (0-100): domain_trust, tool_description_safety,
   permission_scope, supply_chain, community_signal, temporal_stability,
   injection_resilience (Phase 8)
3. Verdict: TRUSTED_GENERAL / TRUSTED_RESEARCH / ENTERPRISE_CONTROLLED /
   CAUTION_LIMITED / HIGH_RISK_ISOLATED / INSUFFICIENT
4. Analyst reviews brief, decides APPROVED / CONDITIONAL / REJECTED
5. Decision logged with expiry and conditions

NOT a proxy. NOT a runtime monitor. Pre-deployment approval only.

---

## Technology Rules

- Language: Python 3.11, FastAPI (APIs), plain Python (daemons)
- DB access ONLY via write_service :8772 — see wiring rules below
- Runtime: /home/workspace/zo_sentinel/
- Every daemon: run() + if __name__ == '__main__': run()
- Every daemon: heartbeat POST /write table=service_health rows={service, last_heartbeat}
- Ports in use: 8772, 8773, 8780, 8781, 8782, 8790, 8792, 8795
- Available: 8783-8789, 8791, 8793, 8794

## Wiring Rules

```python
# CORRECT
requests.post('http://127.0.0.1:8772/write', json={'table':'t','rows':{...},'wait':True})
requests.post('http://127.0.0.1:8772/query', json={'sql':'SELECT ...'})

# NEVER
import duckdb / duckdb.connect()   # no direct DB
INSERT OR IGNORE                   # not DuckDB — use ON CONFLICT
{'row': {...}}                     # wrong key — must be 'rows'
```

---

## DB Schema Reference

See /home/workspace/zo_sentinel/DB_SCHEMA.md for the complete verified table
and column listing. Key gotchas:

- audit_log: `timestamp` (not created_at), `target_server_id` (not server_id)
- mcp_submissions: `mcp_name` (not mcp_identifier), `requested_by` (not requester_name)
- mcp_risk_register: `computed_at` (not last_assessed)
- mcp_policy_rules: `rule_type` + `pattern` (no condition_field/condition_operator)
- service_health: has `status` and `meta` columns

---

## Directive JSON Format

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

Complexity: low <100 lines, medium 100-300, high >300
Priority: 0.95 critical, 0.90 important, 0.85 normal, 0.75 quality pass

### Chain directive naming rule (CRITICAL — builder bug workaround)

When embedding a `next_directive`, the builder currently uses the `task` field
as the output filename if no explicit `output_file` is given in the chain. This
has produced orphan files like `build_pi_quarantine_promoter.py` (note the
`build_` prefix) when the intended output was `pi_quarantine_promoter.py`.

To avoid this bug:
- **Never start a chained `task` name with `build_` or `rebuild_` unless you
  genuinely want that prefix in the output filename.**
- **Always include an explicit `output_file` field in every `next_directive`**,
  even if it duplicates the task name minus the prefix.
- Prefer task names like `pi_harness_runner` over `build_pi_harness_runner`.

---

## Orphan-File Safety Note (read this before generating anything)

The chain-naming bug above produced two orphan files in Phase 8:
`build_pi_quarantine_promoter.py` and `build_pi_quarantine_promoter_auto.py`.
Both have been neutered with refusal stubs, preserved as `.bak.*` for audit.

**Why they were harmless:** these are *daemon* files, not library modules.
Nothing imports them. They are not in supervisord_sentinel_full.conf. They
have no downstream dependents.

**When this bug WOULD cause real damage:** if a future chain directive creates
a library-style file (something meant to be `import`ed by other modules) under
a mangled name. Example failure mode: chain task `build_common_utils` creates
`/home/workspace/zo_sentinel/build_common_utils.py` while other files expect
`import common_utils`. Silent import failure at daemon startup, no smoke test
to catch it because smoke tests are per-file.

**Generator rule to prevent this class of bug:**
- If a proposed new file will be IMPORTED by other files (utility module,
  shared schema, common types): NEVER embed it in a `next_directive` chain.
  Queue it as a top-level directive with an explicit `output_file` field
  matching the intended import name.
- If a proposed new file is a standalone DAEMON (runs via `python3 file.py`,
  never imported): chain is safe, but still include `output_file` explicitly.

---

## DO NOT GENERATE — Forbidden Directive Classes

The generator MUST NOT produce directives for the following, regardless of
what it observes in the registry or logs. These are either meta-tasks the
builder cannot safely execute, or already-covered by dedicated infrastructure.

### Meta-tasks (builder cannot self-repair this way)
- `fix_smoke_import_failures` / `import_fixer` / any "fix broken imports" directive.
  The builder already has an auto-install loop (`[deps] auto-installed: ...`).
  Generating a file called `import_fixer.py` produces orphan code that fails its
  own smoke test. If imports are breaking, queue a rebuild of the specific file,
  not a meta-fixer.
- `regenerate_builder`, `fix_builder`, `patch_builder` — never let the builder
  rewrite itself through a directive; changes to `zo_sentinel_builder.py` must be
  done by a human editor.
- `cleanup_logs`, `truncate_log_files`, `rotate_logs` — operational, not a build task.
- `restart_<service>` — the builder generates code; it does not orchestrate services.
- `fix_orphan_build_files` / `repair_build_pi_*` / anything targeting the
  quarantined orphans listed below. Those files stay neutered; do not regenerate.

### Already covered by dedicated tooling
- Any `schema_refresh` / `refresh_db_schema` — `refresh_schema_doc.py` handles this.
- Any `run_integration_test` / `execute_tests` — smoke tests run automatically at build time.
- Any `deploy_<file>` / `install_<file>` — supervisord config is managed separately.

### Safety-critical — require numbered MUST/MUST NOT rules in description

Any directive touching Phase 8 (prompt-injection harness) or Phase 9
(enterprise integration, AiDr commits, ServiceNow bridges) MUST:
- Enumerate constraints as numbered MUST / MUST NOT rules in the `description`
  field, not as prose or diagrams. LLMs follow numbered constraints more
  reliably than architectural guidance.
- Cite the relevant plan document (PROMPT_INJECTION_PLAN.md etc.) explicitly.
- Never allow the generated file to introduce input() calls or any interactive
  blocking prompt. All review decisions must be LLM-mediated and auditable
  via a JSONL log.

### Naming collisions to avoid
- Never suggest a file name that already exists in `Already Built` below.
- Never append `_v3`, `_v4`, `_final`, `_new` to a filename. Use `_v2` only for a
  deliberate, documented rewrite of the original.
- Never produce files starting with `build_` or `rebuild_` as output filenames
  (these prefixes belong to task names only — see chain directive rule above).

---

## Already Built (never re-generate)

schema.py, schema_v2.py, mcp_scanner.py, mcp_data_seeder.py,
known_threats.py, signal_analyser.py, trust_synthesiser.py,
approval_workflow.py, policy_engine.py, rug_pull_monitor.py,
registry_api.py, threat_intel_ingestor.py, risk_ranker.py,
attestation_engine.py, search_api.py, lookup.py, pipeline_health.py,
integration_test.py, rate_limiter.py, error_reporter.py, config_validator.py,
url_analyser.py, text_patterns.py, db_utils.py, http_retry.py, watch.py,
sentinel_cli.py, sentinel_sdk.py, audit_trail.py, dashboard_api.py,
daily_digest.py, trend_analyser.py, scoring_cache.py, compliance_reporter.py,
alert_manager.py, assessment_scheduler.py, webhook_dispatcher.py,
mcp_fingerprinter.py, deduplicator.py, verdict_explainer.py,
stale_data_cleaner.py, mcp_profiler.py, data_validator.py, mesh_bridge.py,
registry_reconciler.py, report_formatter.py, directive_validator.py,
bulk_assess_api.py, context_injector.py, comparison_api.py,
notification_hub.py, pattern_learner.py, false_positive_tracker.py,
smoke_evolution_agent.py, api_gateway.py, analyst_feedback_loop.py,
backup_service.py, queue_manager.py, metrics_exporter.py,
github_pr_checker.py, exemption_manager.py, npm_webhook_handler.py,
remediation_advisor.py, cve_enricher.py, behavioral_analyser.py,
threat_correlator.py, anomaly_detector.py, similarity_scorer.py,
performance_monitor.py, manifest_blast_radius.py, shodan_exposure_correlator.py,
github_repo_velocity.py, npm_typo_squatter.py, prompt_injection_scanner.py,
context_manipulation_detector.py, sybil_burst_detector.py,
tool_schema_deep_scanner.py, mcp_impersonation_detector.py,
dependency_chain_auditor.py, threat_feed_aggregator.py,
cross_registry_correlator.py, trust_score_time_series.py,
runtime_behaviour_profiler.py, mcp_age_risk_scorer.py,
approval_anomaly_detector.py, vendor_concentration_monitor.py,
certificate_analyser.py, run_schema.py, quick_seed.py,
build_watcher_api.py, ui_server.py, dashboard.html,
compliance_export_service.py, sentinel_status.html, api_health_checker.py,
email_guid_auth.py, advanced_filter_api.py, forensic_detail_api.py,
manual_override_api.py, mesh_sentinel_reporter.py, mcp_submission_portal.html,
incident_webhook_dispatcher.py, pi_corpus_ingest.py, pi_quarantine_reviewer.py,
pi_quarantine_promoter.py, pi_flagged_review_api.py, pi_harness_runner.py,
pi_scorer.py, stateful_trust_monitor.py, signal_analyser_v2.py,
trust_synthesiser_v2.py, graphql_schema_builder.py, threat_feed_api.py,
alert_dispatcher.py, scan_scheduler.py, compliance_exporter.py

### Quarantined orphan files (DO NOT regenerate or "fix"):
- build_pi_quarantine_promoter.py (chain-naming-bug orphan from 20:28 UTC — neutered with refusal stub, original at .bak.20260416_205734)
- build_pi_quarantine_promoter_auto.py (chain-naming-bug orphan from 20:42 UTC — neutered with refusal stub, original at .bak.20260416_205740)
- import_fixer.py (failed smoke 18:58 UTC — meta-task that cannot work; see DO NOT GENERATE)

---

## Phase 8 Status — BUILD COMPLETE

All Phase 8 harness components have been built. Do NOT generate new directives
for these; they are production-ready subject to integration testing:

- pi_corpus_ingest.py ✓ quarantine-compliant fetcher (20:22)
- pi_quarantine_reviewer.py ✓ LLM triage daemon (20:34)
- pi_quarantine_promoter.py ✓ mechanical mover to pi_test_corpus (20:35)
- pi_flagged_review_api.py ✓ FastAPI :8792 review surface (20:37)
- pi_harness_runner.py ✓ executes payloads against APPROVED MCPs (20:08)
- pi_scorer.py ✓ injection_resilience scorer (20:06)

Remaining Phase 8 work is **integration-testing and wiring**, not new code:
- Add all 6 pi_* daemons to supervisord_sentinel_full.conf
- Verify trust_synthesiser_v2 (already built 19:50) reads mcp_signal_scores with
  dimension='injection_resilience' and applies weight 1.6 threshold 0.80.
  If not, queue `rewrite_trust_synthesiser_v3_pi_dimension` as high priority.
- Update attestation_engine.py language to cite dynamic evidence. Queue
  `extend_attestation_for_pi` if not already reflected.

---

## High-Value Targets (remaining)

### Quality passes — priority 0.75
- Review trust_synthesiser_v2.py for the 7th-dimension weighting (may already be correct)
- Review attestation_engine.py for dynamic-evidence language (may need extension)

### Phase 9 candidates — enterprise integration (now UNLOCKED)

Phase 8 is build-complete, so Phase 9 can begin. All complexity=high, use
numbered MUST/MUST NOT rules:

- snow_connector.py: ServiceNow inbound webhook for MCP request tickets.
  Must authenticate via SNOW OAuth; must validate request signature; must
  never accept unsigned webhooks. Priority 0.92.
- aidr_commit_gateway.py: CrowdStrike AI Defense Runtime commit bridge.
  Must check ZO-SENTINEL verdict before forwarding; must never auto-commit
  CAUTION_LIMITED or HIGH_RISK_ISOLATED without explicit override; must
  include injection_resilience score in commit payload. Priority 0.90.
- approval_evidence_bundler.py: produce audit-ready artefact per decision,
  bundling all 7 signal scores, attestation text, pi_results summary,
  corpus hash, and analyst decision metadata into a single signed JSON.
  Priority 0.88.