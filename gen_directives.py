#!/usr/bin/env python3
"""
gen_directives.py -- Generate 100 build directives for ZO-SENTINEL.
Run: python3 /home/workspace/zo_sentinel/gen_directives.py
"""
import json
from pathlib import Path

DIR = Path("/home/workspace/zo_sentinel/directives")
DIR.mkdir(exist_ok=True)

DIRECTIVES = [

# ── QUALITY REWRITES (existing stubs) ──────────────────────────────────────
{"n":"022","task":"rewrite_signal_analyser","file":"signal_analyser.py","c":"high","ph":"3","pri":0.95,
"reads":["schema.py","known_threats.py"],
"desc":"Full signal analyser daemon. ws_query mcp_server_registry for unscored servers (WHERE trust_score IS NULL LIMIT 20). Score 6 signals each 0-100: domain_trust=check TLD and length of URL hostname; tool_description_safety=scan description against HIGH_RISK_PATTERNS from known_threats; permission_scope=count dangerous words (filesystem,credential,secret,exec,shell) in description; supply_chain=check server_id and name against check_package() from known_threats; community_signal=score by registry_source (npm_official=80,github=60,manual=40,unknown=20); temporal_stability=90 if scan_count>5 else 50. ws_write each signal to mcp_signal_scores {server_id,signal_name,score,evidence,scored_at}. ws_write composite trust_score and verdict to mcp_server_registry. Verdict: >=75=TRUSTED_GENERAL,>=60=TRUSTED_RESEARCH,>=50=ENTERPRISE_CONTROLLED,>=35=CAUTION_LIMITED,>=20=HIGH_RISK_ISOLATED,else=INSUFFICIENT. Heartbeat service_health. Poll 1800s.",
"ctx":"Imports from known_threats: check_package,check_domain,HIGH_RISK_PATTERNS. All DB via write_service:8772. Never import duckdb directly."},

{"n":"023","task":"rewrite_risk_ranker","file":"risk_ranker.py","c":"high","ph":"7","pri":0.93,
"reads":["schema.py","signal_analyser.py"],
"desc":"Risk ranker daemon. Reads mcp_server_registry+mcp_signal_scores+mcp_threat_associations via ws_query. Computes risk_rank 0-100: (100-trust_score)*0.4 + threat_count*10*0.3 + staleness_penalty*0.2 + permission_scope_raw*0.1. Clamp 0-100. Map to risk_tier: >=80=CRITICAL,>=60=HIGH,>=40=MEDIUM,else=LOW. ws_write to mcp_risk_register {server_id,name,risk_rank,risk_tier,threat_count,staleness_days,computed_at}. Write RISK_REGISTER.md sorted by risk_rank desc. Poll 14400s. Heartbeat."},

{"n":"024","task":"rewrite_attestation_engine","file":"attestation_engine.py","c":"high","ph":"7","pri":0.92,
"reads":["schema.py","schema_v2.py","trust_synthesiser.py"],
"desc":"Attestation engine daemon. generate_attestation(server_id)->dict. Fetch trust_score+verdict+confidence from mcp_server_registry, risk_tier from mcp_risk_register. Build attestation_text from verdict: TRUSTED_GENERAL=90d expiry no significant indicators; TRUSTED_RESEARCH=60d suitable for R&D; ENTERPRISE_CONTROLLED=60d controlled use only; CAUTION_LIMITED=30d elevated risk isolated env; HIGH_RISK_ISOLATED=7d high risk no sensitive data; KNOWN_THREAT=7d do not deploy; INSUFFICIENT=14d manual review. caveats always=automated analysis only not a security audit. ws_write mcp_attestations {server_id,attestation_text,scope,confidence_level,valid_until,risk_tier,caveats,generated_at}. Write ATTESTATION_REPORT.md. Poll 21600s. Never use words safe or unsafe."},

{"n":"025","task":"rewrite_mcp_scanner","file":"mcp_scanner.py","c":"high","ph":"2","pri":0.91,
"reads":["schema.py"],
"desc":"MCP scanner daemon. Fetches real MCP packages from two sources: (1) npm API: GET https://registry.npmjs.org/-/v1/search?text=scope:modelcontextprotocol&size=250 parse objects[].package {name,description,links.npm,version,date}. (2) GitHub API: GET https://api.github.com/search/repositories?q=topic:mcp-server&sort=stars&per_page=50 parse items[] {name,description,html_url,stargazers_count,pushed_at}. For each: compute server_id=md5(url), set registry_source=npm_official or github, write to mcp_server_registry via ws_write {server_id,name,url,description,registry_source,scan_count=1,first_seen=now,last_scanned=now}. Skip if server_id already exists (check via ws_query). Poll 21600s. Heartbeat."},

{"n":"026","task":"rewrite_rug_pull_monitor","file":"rug_pull_monitor.py","c":"high","ph":"5","pri":0.90,
"reads":["schema.py","known_threats.py"],
"desc":"Rug pull monitor daemon. For each server in mcp_server_registry with verdict=TRUSTED_GENERAL or TRUSTED_RESEARCH: fetch current tool definitions from server URL (GET /tools or /manifest). Compute SHA256 hash of tool definitions JSON. Compare to stored hash in mcp_tool_hashes table (ws_query). If hash changed: ws_write mcp_threat_associations {server_id,threat_type=tool_mutation,evidence=hash_changed:old->new,severity=HIGH,reported_at=now}. Update stored hash. If server unreachable 3x in a row: ws_write threat severity=MEDIUM evidence=server_unreachable. Also re-run check_domain() on all URLs. Poll 21600s. Heartbeat."},

{"n":"027","task":"rewrite_threat_intel_ingestor","file":"threat_intel_ingestor.py","c":"high","ph":"7","pri":0.89,
"reads":["schema.py","known_threats.py"],
"desc":"Threat intel ingestor daemon. Two sources: (1) world_articles table: ws_query world_articles WHERE topics LIKE '%cybersecurity%' AND (title LIKE '%mcp%' OR title LIKE '%model context%' OR title LIKE '%supply chain%') AND processed_at > now()-INTERVAL 48 HOUR. For each article: extract server name clues from title/summary, find matching server_id in mcp_server_registry, ws_write mcp_threat_associations {server_id,threat_type=news_signal,evidence=article title+url,severity based on keywords(critical/high/medium/low)}. (2) OSV.dev: GET https://api.osv.dev/v1/query with ecosystem=npm. Match CVE affected packages against mcp_server_registry names. ws_write severity from CVSS score. Poll 7200s. Heartbeat."},

# ── SHARED UTILITIES ────────────────────────────────────────────────────────
{"n":"028","task":"build_db_utils","file":"db_utils.py","c":"medium","ph":"2","pri":0.88,
"reads":["schema.py"],
"desc":"Shared DB utility module. Functions: ws_query(sql,params=None)->list (POST write_service/query with retry 3x backoff), ws_write(table,row)->bool (POST write_service/write rows field), ws_execute(sql)->bool (POST write_service/execute), ws_heartbeat(service_name) (writes service_health), get_unscored_servers(limit=20)->list, get_server_by_id(server_id)->dict|None, get_server_signals(server_id)->list. All functions handle ConnectionError with 3 retries and 2s backoff. Module-level WRITE_SERVICE=http://127.0.0.1:8772. No daemon, pure utility."},

{"n":"029","task":"build_verdict_taxonomy","file":"verdict_taxonomy.py","c":"low","ph":"2","pri":0.87,
"reads":[],
"desc":"Shared verdict taxonomy constants. VERDICTS list in order: TRUSTED_GENERAL,TRUSTED_RESEARCH,ENTERPRISE_CONTROLLED,CAUTION_LIMITED,HIGH_RISK_ISOLATED,KNOWN_THREAT,INSUFFICIENT. VERDICT_THRESHOLDS dict mapping verdict to min_score. VERDICT_EXPIRY_DAYS dict. RISK_TIERS: CRITICAL,HIGH,MEDIUM,LOW with thresholds. VERDICT_DESCRIPTIONS dict with human-readable one-line description of each verdict. Functions: score_to_verdict(score)->str, verdict_to_expiry(verdict)->int, is_safe_for_production(verdict)->bool (only TRUSTED_GENERAL returns True). No daemon."},

{"n":"030","task":"build_signal_weights","file":"signal_weights.py","c":"low","ph":"3","pri":0.86,
"reads":[],
"desc":"Signal weight configuration. SIGNAL_WEIGHTS dict: domain_trust=0.20,tool_description_safety=0.20,permission_scope=0.15,supply_chain=0.15,community_signal=0.15,temporal_stability=0.15 (must sum to 1.0). SIGNAL_NAMES list. SIGNAL_DESCRIPTIONS dict. compute_trust_score(signals_dict)->float: multiplies each signal score by its weight and sums. validate_weights()->bool: checks sum==1.0. No daemon, pure config module."},

# ── PHASE 9: INTEGRATION & OBSERVABILITY ───────────────────────────────────
{"n":"031","task":"build_pipeline_health","file":"pipeline_health.py","c":"medium","ph":"9","pri":0.85,
"reads":["schema.py"],
"desc":"Pipeline health daemon. Every 14400s: (1) count unscored servers (mcp_server_registry WHERE trust_score IS NULL) (2) count stale assessments (last_assessed < now()-7d) (3) count servers with no attestation (not in mcp_attestations) (4) count servers with no threat intel checked (5) count zero-signal servers (not in mcp_signal_scores). ws_write each count as pipeline_health event to mesh_events with severity=WARNING if any count >10 else INFO. Log summary. Heartbeat."},

{"n":"032","task":"build_integration_test","file":"integration_test.py","c":"high","ph":"9","pri":0.84,
"reads":["schema.py","registry_api.py","approval_workflow.py"],
"desc":"End-to-end integration test suite. Tests: (1) write_service reachable (2) all tables exist via ws_query information_schema (3) at least 1 server in mcp_server_registry (4) GET registry_api /health returns 200 (5) GET registry_api /v1/registry returns list (6) GET approval_workflow /health returns 200 (7) if servers exist: GET /v1/assess?mcp=test returns valid structure (8) mesh_events writable. Each test: PASS/FAIL with reason. Print summary. sys.exit(1) if any FAIL. Write results to mesh_events event_type=integration_test_result. Run via: python3 integration_test.py --write-db."},

{"n":"033","task":"build_assessment_auditor","file":"assessment_auditor.py","c":"medium","ph":"9","pri":0.83,
"reads":["schema.py"],
"desc":"Assessment quality auditor daemon. Every 86400s: reviews recent verdicts for quality issues: (1) servers with verdict but confidence<0.3 (2) servers where all 6 signals are identical score (possible scoring bug) (3) servers assessed >30 days ago not reassessed (4) TRUSTED_GENERAL servers with any HIGH/CRITICAL threat associations (contradiction). For each issue: ws_write corrections table {agent_id=zo_sentinel.auditor,action=assessment_quality_issue,reason=description,cluster=data_quality}. Write AUDIT_REPORT.md. Heartbeat."},

{"n":"034","task":"build_signal_drift_detector","file":"signal_drift_detector.py","c":"medium","ph":"9","pri":0.82,
"reads":["schema.py"],
"desc":"Signal drift detector daemon. Every 43200s: for each server with >2 assessments in mcp_signal_scores history, compare current score vs 7-day-old score per signal. If any signal drifted >20 points: ws_write mesh_events {event_type=signal_drift_detected,payload={server_id,signal,old_score,new_score,delta},severity=WARNING}. If trust_score drifted >15: trigger re-attestation by ws_write mesh_memory {agent_id=zo_sentinel.directive,memory_type=build_directive,content=re-attest task}. Log drift summary. Heartbeat."},

# ── PHASE 10: HARDENING ─────────────────────────────────────────────────────
{"n":"035","task":"build_rate_limiter","file":"rate_limiter.py","c":"medium","ph":"10","pri":0.81,
"reads":[],
"desc":"Rate limiter middleware for FastAPI. RateLimiter class: __init__(requests_per_minute=60,requests_per_hour=500). check(client_ip)->bool: returns True if under limit. Uses in-memory dict with timestamp buckets (no external deps). FastAPI dependency: rate_limit_dependency=Depends(RateLimiter()). If limit exceeded: raise HTTPException(429). Importable by approval_workflow.py and registry_api.py. Also RateLimitMiddleware class for app.add_middleware(). No daemon."},

{"n":"036","task":"build_config_validator","file":"config_validator.py","c":"low","ph":"10","pri":0.80,
"reads":["schema.py"],
"desc":"Config validator. validate_all()->dict with keys: write_service_ok,tables_exist,required_ports_open,env_vars_set,schema_version. validate_write_service(): POST http://127.0.0.1:8772/health timeout=3. validate_tables(): ws_query information_schema for all 8 sentinel tables. validate_ports(): check 8780,8781,8782 with socket connect timeout=1. validate_env(): check MINIMAX_API_KEY set. main() prints pass/fail for each and exits 0 if all pass else 1. Useful as startup check. No daemon."},

{"n":"037","task":"build_error_reporter","file":"error_reporter.py","c":"medium","ph":"10","pri":0.79,
"reads":["schema.py"],
"desc":"Error reporter daemon. Every 86400s: reads mesh_events WHERE event_type IN (build_failed,build_generation_failed,smoke_fail,signal_drift_detected,assessment_quality_issue) AND created_at > now()-24h. Groups by event_type and agent_id. Counts occurrences of each failure pattern. ws_query corrections for recent entries. Writes ERROR_REPORT.md with: summary table, top 5 recurring failures, correction actions taken, trend vs previous day. ws_write summary stats to mesh_events. Heartbeat."},

{"n":"038","task":"build_startup_checker","file":"startup_checker.py","c":"low","ph":"10","pri":0.78,
"reads":["config_validator.py"],
"desc":"Startup checker script. Imports and runs config_validator.validate_all(). If write_service not reachable: print ERROR and exit 1. If tables missing: print which tables missing, offer to run schema.py. If ports not open: print which daemons not started. If env vars missing: print which vars needed. Designed to be sourced at daemon startup. Also: check for GENERATION_FAILURES.md and print count of unresolved failures. No daemon, runs once and exits."},

# ── PHASE 11: DASHBOARD & REPORTING ─────────────────────────────────────────
{"n":"039","task":"build_dashboard_api","file":"dashboard_api.py","c":"high","ph":"11","pri":0.77,
"reads":["schema.py","registry_api.py"],
"desc":"Dashboard data API. FastAPI port 8783. Endpoints: GET /dashboard/summary (total servers, breakdown by verdict, breakdown by risk_tier, avg trust_score, last_scan_time, pipeline_health counts), GET /dashboard/recent (last 20 build events from mesh_events), GET /dashboard/top_risks (top 10 servers by risk_rank from mcp_risk_register JOIN mcp_server_registry), GET /dashboard/trends (verdict distribution last 7 days grouped by date), GET /health. All queries via ws_query. Daemon with uvicorn port 8783. Heartbeat."},

{"n":"040","task":"build_daily_digest","file":"daily_digest.py","c":"medium","ph":"11","pri":0.76,
"reads":["schema.py"],
"desc":"Daily digest generator daemon. Runs at 07:00 UTC daily (check hour in loop). Generates DAILY_DIGEST.md with: new MCPs discovered (24h), verdicts changed, new threat intel, risk tier changes, top 5 highest risk servers, pipeline health summary, build activity summary. ws_write digest summary to mesh_events event_type=daily_digest. Attempt email to robin.craib@gmail.com via api.zo.computer/zo/notify best-effort. Poll 3600s checking hour==7 UTC. Heartbeat."},

{"n":"041","task":"build_trend_analyser","file":"trend_analyser.py","c":"medium","ph":"11","pri":0.75,
"reads":["schema.py"],
"desc":"Trend analyser daemon. Every 43200s: queries mcp_server_registry with last_assessed grouped by date. Computes: verdict distribution over time, avg trust_score trend (7d/14d/30d rolling), new servers per day, threat count trend, risk tier distribution change. Writes TREND_REPORT.md with ASCII bar charts using | characters. ws_write trend summary to mesh_events. Identifies: improving_security (trust_score trend +), degrading_security (trust_score trend -), rapid_growth (>5 new servers per day). Heartbeat."},

{"n":"042","task":"build_compliance_reporter","file":"compliance_reporter.py","c":"medium","ph":"11","pri":0.74,
"reads":["schema.py","attestation_engine.py"],
"desc":"Compliance reporter. Generates COMPLIANCE_REPORT.md. Sections: (1) Executive Summary: total MCPs, approved count, blocked count, pending review (2) Risk Distribution: CRITICAL/HIGH/MEDIUM/LOW counts with percentages (3) Expired Attestations: servers where valid_until < now() (4) Policy Violations: BLOCK decisions from mcp_decisions (5) Recommendations: top 3 actions based on data. Also: generate_csv() exports mcp_server_registry+verdicts as CSV to reports/registry_export.csv. Run via python3 compliance_reporter.py --report or --csv. No daemon."},

# ── PHASE 11: ALERTING ──────────────────────────────────────────────────────
{"n":"043","task":"build_alert_manager","file":"alert_manager.py","c":"medium","ph":"11","pri":0.73,
"reads":["schema.py"],
"desc":"Alert manager daemon. Polls mesh_events every 300s for severity=CRITICAL or CRITICAL events from zo_sentinel agents. Deduplicates: same server_id+event_type within 1h = skip. For each unique alert: (1) write to corrections table (2) attempt email via api.zo.computer/zo/notify (3) write ALERT_LOG.md entry. Thresholds: new KNOWN_THREAT verdict=CRITICAL, 3+ HIGH_RISK servers in 1h=HIGH, attestation expired+still deployed=HIGH. Tracks alert history in alert_history dict to avoid spam. Heartbeat."},

{"n":"044","task":"build_webhook_dispatcher","file":"webhook_dispatcher.py","c":"medium","ph":"11","pri":0.72,
"reads":["schema.py"],
"desc":"Webhook dispatcher daemon. Reads WEBHOOK_URLS from env var (comma-separated). Polls mesh_events every 60s for new severity=CRITICAL or WARNING events with event_type in (build_generation_failed,signal_drift_detected,new_threat_detected). For each: POST to each webhook URL with JSON {event_type,severity,payload,timestamp,source=zo_sentinel}. Retry 3x with 5s backoff. Log success/failure per webhook. Graceful degradation if no WEBHOOK_URLS set (logs warning and skips). Heartbeat."},

# ── PHASE 12: DATA QUALITY ──────────────────────────────────────────────────
{"n":"045","task":"build_deduplicator","file":"deduplicator.py","c":"medium","ph":"12","pri":0.71,
"reads":["schema.py"],
"desc":"Registry deduplicator daemon. Every 86400s: find duplicate MCPs in mcp_server_registry by (1) identical URL (2) name similarity >90% using simple character overlap ratio (3) same npm package different versions. For duplicates: keep highest scan_count entry, merge signal scores, merge threat associations to canonical server_id, delete duplicates via ws_write corrections noting dedup action. Write DEDUP_REPORT.md with actions taken. ws_write dedup_complete to mesh_events. Heartbeat."},

{"n":"046","task":"build_stale_data_cleaner","file":"stale_data_cleaner.py","c":"medium","ph":"12","pri":0.70,
"reads":["schema.py"],
"desc":"Stale data cleaner daemon. Weekly (check day_of_week==0 in loop): (1) find mcp_signal_scores older than 90d for servers that have been reassessed -> mark old scores inactive via ws_write (2) find mcp_attestations past valid_until -> ws_write status=expired (3) find mcp_server_registry last_scanned > 60d with no recent assessments -> set status=stale (4) archive mesh_memory older than 30d with importance < 0.5. Log counts. ws_write cleanup_complete to mesh_events. Never hard-delete. Poll 86400s. Heartbeat."},

{"n":"047","task":"build_data_validator","file":"data_validator.py","c":"medium","ph":"12","pri":0.69,
"reads":["schema.py","verdict_taxonomy.py"],
"desc":"Data validator daemon. Every 21600s: validates data integrity in mcp_server_registry: (1) servers with trust_score outside 0-100 range (2) invalid verdict values not in VERDICTS list (3) missing required fields (server_id,name,url) (4) mcp_signal_scores with score outside 0-100 (5) mcp_attestations with valid_until in past and status not expired. For each violation: ws_write corrections {agent_id=zo_sentinel.validator,action=data_integrity_violation,reason=description}. Log violations. ws_write validation_complete to mesh_events. Heartbeat."},

{"n":"048","task":"build_registry_reconciler","file":"registry_reconciler.py","c":"medium","ph":"12","pri":0.68,
"reads":["schema.py","mcp_scanner.py"],
"desc":"Registry reconciler daemon. Every 43200s: fetches fresh package list from npm @modelcontextprotocol scope. Compares against mcp_server_registry. New packages not in registry -> ws_write as new entries. Packages in registry but removed from npm -> set status=deprecated in registry. Version updates -> increment scan_count, update last_scanned. Also reconcile GitHub topic:mcp-server list. Log new/deprecated/updated counts. ws_write reconciliation_complete event. Heartbeat."},

# ── PHASE 12: API EXTENSIONS ─────────────────────────────────────────────────
{"n":"049","task":"build_bulk_assess_api","file":"bulk_assess_api.py","c":"high","ph":"12","pri":0.67,
"reads":["schema.py","registry_api.py"],
"desc":"Bulk assessment API. FastAPI port 8784. POST /v1/bulk/assess body={mcps:[list of name or url strings]} -> triggers scoring for up to 20 MCPs per request. Returns {submitted:int,job_id:str}. GET /v1/bulk/status/{job_id} -> {status:pending|complete,results:[{mcp,verdict,trust_score}]}. POST /v1/bulk/import body={servers:[{name,url,description}]} -> bulk import to mcp_server_registry. GET /v1/export/csv -> streams CSV of full registry. GET /health. Daemon with uvicorn port 8784. Heartbeat."},

{"n":"050","task":"build_comparison_api","file":"comparison_api.py","c":"medium","ph":"12","pri":0.66,
"reads":["schema.py","registry_api.py"],
"desc":"MCP comparison API. FastAPI port 8785. GET /v1/compare?a={server_id_or_name}&b={server_id_or_name} -> side-by-side comparison of two MCPs: both registry records, signal scores per dimension, verdicts, risk tiers, attestation status, threat count. Returns structured JSON with winner per signal dimension and overall_recommendation (prefer_a|prefer_b|equivalent|both_risky). GET /v1/rank?tier={risk_tier}&limit=10 -> ranked list within tier. GET /health. Daemon uvicorn port 8785. Heartbeat."},

# ── PHASE 13: INTELLIGENCE ──────────────────────────────────────────────────
{"n":"051","task":"build_pattern_learner","file":"pattern_learner.py","c":"high","ph":"13","pri":0.65,
"reads":["schema.py"],
"desc":"Pattern learner daemon. Every 86400s: reads mcp_decisions from schema_v2 (analyst approvals/rejections). For REJECTED decisions: extract text patterns from server description that correlate with rejection. Updates KNOWLEDGE_BASE.md with: LEARNED_REJECTION_PATTERNS list of description substrings found in 3+ rejections. For APPROVED despite low trust_score: extract characteristics of false positives. ws_write learned_patterns count to mesh_events. Write LEARNING_REPORT.md. Heartbeat."},

{"n":"052","task":"build_false_positive_tracker","file":"false_positive_tracker.py","c":"medium","ph":"13","pri":0.64,
"reads":["schema.py"],
"desc":"False positive tracker. Every 43200s: finds servers where analyst decision=APPROVED but verdict was HIGH_RISK_ISOLATED or KNOWN_THREAT (false positives) or REJECTED but verdict was TRUSTED_GENERAL (false negatives). For each: ws_write corrections {action=false_positive_detected or false_negative_detected, reason=verdict vs decision mismatch}. Compute precision and recall metrics. Write FALSE_POSITIVE_REPORT.md. These metrics feed back to signal_weights.py tuning. Heartbeat."},

{"n":"053","task":"build_analyst_feedback_loop","file":"analyst_feedback_loop.py","c":"medium","ph":"13","pri":0.63,
"reads":["schema.py","signal_weights.py"],
"desc":"Analyst feedback loop daemon. Every 86400s: reads mcp_decisions where analyst overrode system verdict. Adjusts signal weights based on which signals were most predictive of correct outcome. If domain_trust correctly predicted 80% of analyst decisions but permission_scope only 40%: suggest weight increase for domain_trust. Writes WEIGHT_SUGGESTIONS.md with recommended weight adjustments. Does NOT automatically change weights (human review required). ws_write feedback_processed to mesh_events. Heartbeat."},

# ── PHASE 13: OPERATIONS ────────────────────────────────────────────────────
{"n":"054","task":"build_backup_service","file":"backup_service.py","c":"medium","ph":"13","pri":0.62,
"reads":["schema.py"],
"desc":"Backup service daemon. Weekly: export mcp_server_registry,mcp_signal_scores,mcp_attestations,mcp_decisions to JSON files in /home/workspace/zo_sentinel/backups/YYYY-MM-DD/. Use ws_query to fetch all rows. Write one JSON file per table. Compress with gzip. Keep last 4 weekly backups (delete older). Write backup manifest BACKUP_MANIFEST.md. ws_write backup_complete to mesh_events with backup_path and row_counts. Poll 86400s check day_of_week==0. Heartbeat."},

{"n":"055","task":"build_performance_monitor","file":"performance_monitor.py","c":"medium","ph":"13","pri":0.61,
"reads":["schema.py"],
"desc":"Performance monitor daemon. Every 300s: measures response time for write_service /health, registry_api /health (port 8781), approval_workflow /health (port 8780), search_api /health (port 8782). Records latency ms in perf_metrics dict. If any service >500ms avg over 3 readings: ws_write mesh_events severity=WARNING event_type=performance_degradation. If service unreachable: severity=CRITICAL. Write PERFORMANCE_LOG.md with rolling 24h stats. Heartbeat."},

{"n":"056","task":"build_queue_manager","file":"queue_manager.py","c":"medium","ph":"13","pri":0.60,
"reads":["schema.py"],
"desc":"Assessment queue manager. Manages prioritization of which MCPs to assess next. GET queue of unscored servers. Priority order: (1) servers explicitly submitted via approval_workflow (highest) (2) servers discovered from npm official scope (3) servers with high scan_count but no verdict (4) servers last assessed >7d ago. Writes ordered queue to ASSESSMENT_QUEUE.md. Exposes: get_next_batch(n=10)->list of server_ids in priority order. Used by signal_analyser to pick which servers to score. No daemon, utility module."},

# ── PHASE 14: INTEGRATIONS ──────────────────────────────────────────────────
{"n":"057","task":"build_github_pr_checker","file":"github_pr_checker.py","c":"high","ph":"14","pri":0.59,
"reads":["schema.py","registry_api.py"],
"desc":"GitHub PR checker utility. check_pr_for_mcps(pr_url)->list: fetches PR diff from GitHub API, finds any new MCP package.json entries or mcp_config additions. For each new MCP found: looks up in mcp_server_registry, returns verdict+trust_score+risk_tier. generate_pr_comment(results)->str: builds markdown PR comment summarising MCP safety assessment with emoji indicators (green=trusted, yellow=caution, red=high_risk). Intended to be called from a CI webhook. Not a daemon. Requires GITHUB_TOKEN env var."},

{"n":"058","task":"build_npm_webhook_handler","file":"npm_webhook_handler.py","c":"medium","ph":"14","pri":0.58,
"reads":["schema.py","mcp_scanner.py"],
"desc":"npm webhook handler. FastAPI port 8786. POST /webhook/npm receives npm registry change notifications. Parses {name,version,description,dist.tarball}. If name contains modelcontextprotocol or mcp-server: ws_write to mcp_server_registry as new entry. Trigger signal scoring by ws_write mesh_memory directive. Returns {accepted:true,server_id:str}. GET /health. Validates X-npm-Signature header if WEBHOOK_SECRET env var set. Daemon uvicorn port 8786. Heartbeat."},

# ── PHASE 14: EXTENDED THREAT INTEL ─────────────────────────────────────────
{"n":"059","task":"build_cve_enricher","file":"cve_enricher.py","c":"high","ph":"14","pri":0.57,
"reads":["schema.py","known_threats.py"],
"desc":"CVE enricher daemon. Every 21600s: fetches recent CVEs from https://services.nvd.nist.gov/rest/json/cves/2.0?keywordSearch=mcp+model+context+protocol&resultsPerPage=50. For each CVE: extract affected package names, find matching servers in mcp_server_registry by name similarity, ws_write mcp_threat_associations {server_id,threat_type=cve,evidence=CVE-ID+description[:200],severity=from CVSS score: >=9=CRITICAL,>=7=HIGH,>=4=MEDIUM,else=LOW}. Also update known_threats HIGH_RISK_PATTERNS if new patterns emerge. Write CVE_ENRICHMENT_LOG.md. Heartbeat."},

{"n":"060","task":"build_behavioral_analyser","file":"behavioral_analyser.py","c":"high","ph":"14","pri":0.56,
"reads":["schema.py"],
"desc":"Behavioral analyser daemon. Every 43200s: analyses patterns in mcp_server_registry over time. Detects: (1) rapid_name_change: servers that changed names between scans (2) description_injection: descriptions containing HTML/JS/SQL injection patterns (3) permission_escalation: tool_definitions requesting more permissions than initial scan (4) namespace_squatting: names very similar to popular trusted MCPs (edit distance <3) (5) phantom_packages: npm packages with 0 downloads but >5 stars (unusual ratio). ws_write detected behaviors to mcp_threat_associations. Heartbeat."},

# ── PHASE 15: ML ENHANCEMENT ────────────────────────────────────────────────
{"n":"061","task":"build_anomaly_detector","file":"anomaly_detector.py","c":"high","ph":"15","pri":0.55,
"reads":["schema.py","signal_analyser.py"],
"desc":"Anomaly detector daemon. Every 43200s: computes population statistics for each signal across all scored servers (mean, stddev). Flags servers where any signal is >2 stddev from mean as statistical_anomaly. Also detects: (1) score_clustering_anomaly: if 90% of servers have near-identical scores (scoring not discriminating) (2) temporal_anomaly: server score changed >30 points between assessments with no new threat intel. ws_write anomalies to mcp_threat_associations severity=MEDIUM evidence=statistical. Write ANOMALY_REPORT.md. Heartbeat."},

{"n":"062","task":"build_similarity_scorer","file":"similarity_scorer.py","c":"medium","ph":"15","pri":0.54,
"reads":["schema.py","known_threats.py"],
"desc":"Similarity scorer utility. Uses character n-gram overlap (no ML dependencies) to find suspicious similarities. Functions: name_similarity(a,b)->float 0-1, description_similarity(a,b)->float, find_similar_to_known_threats(server_id)->list of matches with score. find_namespace_squatting(threshold=0.85)->list of (server_a,server_b,similarity) pairs. Called by behavioral_analyser and signal_analyser. No daemon, utility module. No numpy/sklearn required."},

# ── ADDITIONAL QUALITY DIRECTIVES ────────────────────────────────────────────
{"n":"063","task":"rewrite_lookup_cli","file":"lookup.py","c":"medium","ph":"8","pri":0.88,
"reads":["schema.py"],
"desc":"CLI lookup tool. python3 lookup.py <mcp_name_or_url>. Args: positional=name_or_url, --threats=show threat associations, --risks=show risk register entry, --stats=show signal scores table, --json=output raw JSON, --all=show everything. Queries write_service for registry+signals+threats+risk+attestation. Terminal output: colour-coded using ANSI codes (red=HIGH_RISK/KNOWN_THREAT,yellow=CAUTION,green=TRUSTED). Print: name, verdict badge, trust_score bar, top 3 signals, latest attestation summary, threat count. Graceful if no data found."},

{"n":"064","task":"rewrite_search_api","file":"search_api.py","c":"high","ph":"8","pri":0.87,
"reads":["schema.py","registry_api.py"],
"desc":"Search API. FastAPI port 8782. GET /search?q=&limit=20&verdict=&risk_tier= -> ILIKE search on name+description in mcp_server_registry with optional verdict/risk_tier filters. Returns list with server_id,name,url,verdict,trust_score,risk_tier. GET /mcp/{server_id} -> full detail: registry record + all signals + all threats + risk register + latest attestation. GET /threats?severity=&limit=20 -> threat feed. GET /risks?tier=&limit=20 -> risk register. GET /stats -> counts by verdict and risk_tier. GET /health. Daemon uvicorn port 8782. Heartbeat."},

# ── SUPERVISORD & STARTUP ────────────────────────────────────────────────────
{"n":"065","task":"build_start_all_sh","file":"start_all.sh","c":"low","ph":"10","pri":0.76,
"reads":[],
"desc":"Bash startup script. #!/bin/bash. Starts all ZO-SENTINEL daemons in order: (1) validate schema exists: python3 config_validator.py || (python3 schema.py && python3 schema_v2.py) (2) nohup python3 mcp_scanner.py (3) nohup python3 signal_analyser.py (4) nohup python3 trust_synthesiser.py (5) nohup python3 threat_intel_ingestor.py (6) nohup python3 risk_ranker.py (7) nohup python3 attestation_engine.py (8) nohup python3 approval_workflow.py (9) nohup python3 registry_api.py (10) nohup python3 search_api.py. Each redirected to logs/. Prints PID for each. Sleep 2 between daemons."},

{"n":"066","task":"build_status_sh","file":"status.sh","c":"low","ph":"10","pri":0.75,
"reads":[],
"desc":"Bash status script. #!/bin/bash. For each daemon (mcp_scanner,signal_analyser,trust_synthesiser,threat_intel_ingestor,risk_ranker,attestation_engine,approval_workflow,registry_api,search_api): check if pgrep -f <daemon>.py returns a PID. Print [OK] PID or [--] not running. Then curl -s http://localhost:<port>/health for API services. Print summary: N/M services running. Also print last 3 lines of each log file."},

# ── ADDITIONAL TEST UTILITIES ────────────────────────────────────────────────
{"n":"067","task":"build_test_scoring","file":"tests/test_scoring.py","c":"medium","ph":"9","pri":0.72,
"reads":["signal_weights.py","verdict_taxonomy.py"],
"desc":"Unit tests for scoring functions. Tests: (1) score_to_verdict at boundary values (75,74,60,59,50,49,35,34,20,19) (2) compute_trust_score with known inputs returns expected output (3) weights sum to 1.0 (4) all VERDICTS in VERDICT_THRESHOLDS (5) attestation expiry days are positive integers. Uses stdlib unittest. Run: python3 -m pytest tests/test_scoring.py -v. No daemon."},

{"n":"068","task":"build_test_known_threats","file":"tests/test_known_threats.py","c":"medium","ph":"9","pri":0.71,
"reads":["known_threats.py"],
"desc":"Unit tests for known_threats.py. Tests: (1) check_package with known malicious names returns True (2) check_package with clean names returns False (3) check_domain with suspicious TLDs returns True (4) check_domain with clean domains returns False (5) HIGH_RISK_PATTERNS is non-empty list (6) SUSPICIOUS_PERMISSIONS is non-empty list (7) each pattern in HIGH_RISK_PATTERNS is a non-empty string. Uses stdlib unittest. Run: python3 -m pytest tests/test_known_threats.py -v. No daemon."},

{"n":"069","task":"build_test_wiring","file":"tests/test_wiring.py","c":"medium","ph":"9","pri":0.70,
"reads":[],
"desc":"Wiring contract tests. Imports each sentinel module and checks: (1) no module imports duckdb directly (2) all daemons have run() function (3) all FastAPI apps have /health route defined (4) all modules have __name__==__main__ guard if they have run(). Uses importlib to load each module. Checks ast.parse for duckdb.connect pattern. Writes WIRING_TEST_REPORT.md with pass/fail per module. Run: python3 tests/test_wiring.py. sys.exit(1) on any failure."},

{"n":"070","task":"build_mock_write_service","file":"tests/mock_write_service.py","c":"medium","ph":"9","pri":0.69,
"reads":[],
"desc":"Mock write service for testing. FastAPI port 8799 (test port). Stores all writes in memory dict by table name. Endpoints: POST /write (stores rows in memory), POST /query (returns stored rows matching basic SQL WHERE), POST /execute (no-op returns ok), GET /health, GET /dump (returns all stored data as JSON), POST /reset (clears all data). Used by integration tests to avoid polluting real DuckDB. Run: python3 tests/mock_write_service.py --port 8799 in test setup."},

# ── ADDITIONAL UTILITY MODULES ───────────────────────────────────────────────
{"n":"071","task":"build_http_retry","file":"http_retry.py","c":"low","ph":"2","pri":0.85,
"reads":[],
"desc":"HTTP retry utility. Functions: get_with_retry(url,params=None,headers=None,retries=3,backoff=2.0,timeout=10)->Response|None. post_with_retry(url,json=None,headers=None,retries=3,backoff=2.0,timeout=10)->Response|None. Both retry on ConnectionError,Timeout,5xx responses. Exponential backoff: wait=backoff**attempt. Returns None if all retries exhausted (logs warning). Also: safe_json(response)->dict|None catches json.JSONDecodeError. Designed to replace raw requests.get/post in all sentinel modules. No daemon."},

{"n":"072","task":"build_text_patterns","file":"text_patterns.py","c":"low","ph":"3","pri":0.84,
"reads":["known_threats.py"],
"desc":"Extended text pattern library. Extends known_threats.py. INJECTION_PATTERNS: list of strings indicating prompt injection attempts in tool descriptions (ignore previous, disregard instructions, system prompt, act as). CREDENTIAL_HARVESTING_PATTERNS: patterns suggesting credential theft (send credentials, exfiltrate, upload private key, env var dump). OBFUSCATION_PATTERNS: base64 strings in descriptions, unicode lookalikes, zero-width chars. Functions: scan_description(text)->dict {injections:list,credentials:list,obfuscation:list,score_penalty:int}. No daemon."},

{"n":"073","task":"build_url_analyser","file":"url_analyser.py","c":"low","ph":"3","pri":0.83,
"reads":["known_threats.py"],
"desc":"URL analysis utility. Functions: analyse_url(url)->dict {domain,tld,is_ip_address,is_localhost,is_suspicious_tld,domain_length,has_port,port,path_depth,score:int 0-100}. SUSPICIOUS_TLDS: list including .xyz,.top,.click,.pw,.cc,.tk. is_suspicious(url)->bool. domain_trust_score(url)->int 0-100: penalise short domains(-20),suspicious TLD(-30),IP address(-40),localhost(-100),no HTTPS(-10),unusual port(-20). Called by signal_analyser domain_trust signal. No daemon."},

{"n":"074","task":"build_report_formatter","file":"report_formatter.py","c":"low","ph":"11","pri":0.68,
"reads":[],
"desc":"Markdown report formatter utility. Functions: header(text,level=1)->str, table(headers,rows)->str (markdown table), badge(text,style)->str (CRITICAL/HIGH/MEDIUM/LOW emoji badges), progress_bar(value,max_val,width=20)->str (ASCII ████░░░ bar), section(title,content)->str. verdict_badge(verdict)->str: maps verdict to emoji+text (TRUSTED_GENERAL=✅,TRUSTED_RESEARCH=🔵,ENTERPRISE_CONTROLLED=🟡,CAUTION_LIMITED=🟠,HIGH_RISK_ISOLATED=🔴,KNOWN_THREAT=💀,INSUFFICIENT=⚪). Used by all report-generating daemons. No daemon."},

# ── CONFIGURATION & SECRETS ──────────────────────────────────────────────────
{"n":"075","task":"build_env_config","file":"env_config.py","c":"low","ph":"2","pri":0.86,
"reads":[],
"desc":"Environment configuration module. Reads all config from environment variables with defaults. WRITE_SERVICE_URL=os.getenv(WRITE_SERVICE_URL,http://127.0.0.1:8772). OLLAMA_URL=os.getenv(OLLAMA_URL,http://127.0.0.1:11434). MINIMAX_API_KEY=os.getenv(MINIMAX_API_KEY,). GITHUB_TOKEN=os.getenv(GITHUB_TOKEN,). WEBHOOK_SECRET=os.getenv(WEBHOOK_SECRET,). ALERT_EMAIL=os.getenv(ALERT_EMAIL,robin.craib@gmail.com). PORT_APPROVAL=int(os.getenv(PORT_APPROVAL,8780)). PORT_REGISTRY=int(os.getenv(PORT_REGISTRY,8781)). PORT_SEARCH=int(os.getenv(PORT_SEARCH,8782)). validate() checks required vars set. No daemon."},

# ── DOCS & KNOWLEDGE ────────────────────────────────────────────────────────
{"n":"076","task":"build_knowledge_base_seed","file":"KNOWLEDGE_BASE.md","c":"low","ph":"2","pri":0.94,
"reads":[],
"desc":"Seed the KNOWLEDGE_BASE.md file that all generation prompts inject. Content: CRITICAL RULES section: never import duckdb directly, always use write_service POST /write with rows field not row, all daemons need run() and __main__ guard, heartbeat pattern, port assignments (8772=write_service,8773=inference_router,8780=approval,8781=registry,8782=search,8783=dashboard,8784=bulk,8785=compare), verdict taxonomy, write_service query pattern. COMMON ERRORS section listing the top 5 wiring mistakes seen in failed builds. PATTERNS section with working code snippets for ws_query and ws_write."},

# ── PHASE 2B BOOTSTRAP ───────────────────────────────────────────────────────
{"n":"077","task":"build_schema_runner","file":"run_schema.py","c":"low","ph":"2","pri":0.98,
"reads":["schema.py","schema_v2.py"],
"desc":"Schema runner one-shot script. Imports and calls schema.create_all() then schema_v2.create_v2() then schema_v2.seed_default_policies(). Prints count of tables created. Then verifies: ws_query information_schema for all expected table names. Prints PASS/FAIL for each table. sys.exit(0) if all tables exist else sys.exit(1). Run once after fresh deployment: python3 run_schema.py. No daemon."},

{"n":"078","task":"build_quick_seed","file":"quick_seed.py","c":"medium","ph":"2","pri":0.96,
"reads":["schema.py","db_utils.py"],
"desc":"Quick seed script. Seeds exactly 25 well-known MCP servers directly into mcp_server_registry without scanning. Hardcoded list includes: @modelcontextprotocol/server-filesystem, @modelcontextprotocol/server-github, @modelcontextprotocol/server-slack, @modelcontextprotocol/server-postgres, @modelcontextprotocol/server-brave-search, @modelcontextprotocol/server-puppeteer, @modelcontextprotocol/server-google-maps, @modelcontextprotocol/server-sqlite, @modelcontextprotocol/server-memory, @modelcontextprotocol/server-sequential-thinking plus 15 more community MCPs. Each has name,url,description,registry_source=npm_official,scan_count=1. Run once: python3 quick_seed.py. Prints seeded count."},

# ── ADDITIONAL HIGH-VALUE MODULES ────────────────────────────────────────────
{"n":"079","task":"build_verdict_explainer","file":"verdict_explainer.py","c":"medium","ph":"7","pri":0.71,
"reads":["verdict_taxonomy.py","signal_weights.py"],
"desc":"Verdict explanation generator. explain_verdict(server_id)->str: fetches trust_score,verdict,signals from write_service. Generates human-readable paragraph explaining WHY the server received its verdict. Format: <name> received verdict <VERDICT> (trust score: <N>/100). The strongest contributing factor was <top_signal> (score: <N>/100): <evidence>. This was partially offset/reinforced by <second_signal>. The assessment carries <confidence>% confidence based on <N> scoring dimensions. Full explanation suitable for analyst review notes. No daemon, utility."},

{"n":"080","task":"build_mcp_profiler","file":"mcp_profiler.py","c":"medium","ph":"8","pri":0.70,
"reads":["schema.py","url_analyser.py"],
"desc":"MCP profiler utility. profile_mcp(url)->dict: attempts to fetch the MCP server manifest or package.json. Extracts: tool_count, tool_names list, permission_list, server_type (npm/github/custom), has_authentication, has_encryption, declared_scope. Also runs url_analyser.analyse_url. Returns structured profile dict. Used as pre-assessment enrichment step before signal scoring. Handles timeout gracefully (5s). Stores profile in mcp_server_registry extended_metadata JSON field. No daemon."},

{"n":"081","task":"build_submission_validator","file":"submission_validator.py","c":"medium","ph":"4","pri":0.82,
"reads":["schema.py","known_threats.py","url_analyser.py"],
"desc":"Submission validator for approval_workflow. validate_submission(name,url,description,requested_by)->dict {valid:bool,errors:list,warnings:list,pre_checks:dict}. Checks: url is valid and reachable (HEAD request 5s timeout), not in KNOWN_MALICIOUS_PACKAGES, description not empty, url not localhost/internal IP, not duplicate submission (check mcp_submissions). pre_checks: url_analysis result, known_threat_check result. Returns structured result. Imported by approval_workflow POST /api/submit endpoint. No daemon."},

{"n":"082","task":"build_policy_engine_v2","file":"policy_engine_v2.py","c":"high","ph":"4","pri":0.85,
"reads":["schema_v2.py","verdict_taxonomy.py"],
"desc":"Enhanced policy engine v2. evaluate_policy(server_id,trust_score,verdict,context={})->PolicyDecision. PolicyDecision dataclass: decision=BLOCK|ESCALATE|CONDITIONAL_ALLOW|ALLOW, conditions=list, rationale=str, policy_ids_matched=list. Loads active policy rules from mcp_policy_rules via ws_query. Rules evaluated in priority order. Default rules: KNOWN_THREAT->BLOCK, HIGH_RISK_ISOLATED+no_analyst_override->ESCALATE, trust_score<35->ESCALATE, TRUSTED_GENERAL->ALLOW. Context can include: environment=production|staging|research, data_sensitivity=high|medium|low. Store decision to mcp_decisions via ws_write. No daemon."},

{"n":"083","task":"build_audit_trail","file":"audit_trail.py","c":"medium","ph":"13","pri":0.63,
"reads":["schema.py"],
"desc":"Immutable audit trail module. record_event(event_type,actor,target_server_id,action,outcome,details={})->str: writes to audit_log table via ws_write {event_id=uuid,event_type,actor,target_server_id,action,outcome,details_json,timestamp,immutable=True}. get_server_history(server_id)->list: full chronological history for one server. get_actor_history(actor)->list: all actions by one actor. export_audit_csv(start_date,end_date)->str: CSV of audit log. Used by approval_workflow for all decisions and by all agents making verdict changes. No daemon, utility module."},

{"n":"084","task":"build_notification_hub","file":"notification_hub.py","c":"medium","ph":"11","pri":0.66,
"reads":["schema.py"],
"desc":"Notification hub utility. Centralises all outbound notifications. send_alert(subject,body,severity=HIGH)->bool: tries api.zo.computer/zo/notify with 8s timeout, falls back to writing ALERT_QUEUE.md. send_build_failure(task,description,reason)->bool: formats failure notification for Claude chat ingestion. send_daily_digest(content)->bool. notify_high_risk(server_name,verdict,trust_score)->bool. All methods: best-effort, never raise exceptions, always return bool success. Tracks send_count and failure_count. No daemon."},

{"n":"085","task":"build_assessment_scheduler","file":"assessment_scheduler.py","c":"medium","ph":"9","pri":0.73,
"reads":["schema.py","queue_manager.py"],
"desc":"Assessment scheduler daemon. Coordinates reassessment timing to avoid all daemons running simultaneously. Maintains SCHEDULE.md with next_run times per daemon. Priority reassessment: servers with expired attestations, servers with new threat intel since last assessment, servers where verdict=INSUFFICIENT and 24h have passed. Emits schedule_trigger mesh_events that signal_analyser and trust_synthesiser listen to. Poll 900s. Ensures max 5 concurrent assessments. Heartbeat."},

# ── FINAL BATCH: MISC HIGH VALUE ─────────────────────────────────────────────
{"n":"086","task":"build_mcp_fingerprinter","file":"mcp_fingerprinter.py","c":"medium","ph":"5","pri":0.72,
"reads":["schema.py","text_patterns.py"],
"desc":"MCP fingerprinter. generate_fingerprint(server_id)->dict: creates a stable behavioral fingerprint for an MCP server combining: tool_name_hash (SHA256 of sorted tool names), description_tokens (top 20 significant words), permission_scope_hash, domain_fingerprint, version_string. Fingerprints stored in mcp_fingerprints table. compare_fingerprints(fp_a,fp_b)->float 0-1 similarity. detect_impersonation(server_id)->list: finds existing servers with >0.8 fingerprint similarity. Used by rug_pull_monitor and behavioral_analyser. No daemon."},

{"n":"087","task":"build_scoring_cache","file":"scoring_cache.py","c":"low","ph":"3","pri":0.75,
"reads":[],
"desc":"In-memory scoring cache with TTL. ScoringCache class: get(server_id)->dict|None, set(server_id,data,ttl_seconds=3600), invalidate(server_id), clear_expired(). Uses dict with {data,expires_at} per server_id. Thread-safe with threading.Lock. clear_expired() called on every get to avoid unbounded growth. Global instance: _cache=ScoringCache(). Reduces repeated ws_query calls when signal_analyser processes same server multiple times per cycle. No daemon."},

{"n":"088","task":"build_threat_correlator","file":"threat_correlator.py","c":"high","ph":"14","pri":0.56,
"reads":["schema.py","known_threats.py"],
"desc":"Threat correlator daemon. Every 43200s: finds clusters of related threats across servers. If 3+ servers share the same npm author AND have threat_type=tool_mutation: emit supply_chain_compromise event. If same CVE affects multiple servers: emit coordinated_vulnerability event. If servers from same GitHub org show HIGH_RISK_ISOLATED verdict: emit org_risk_pattern event. ws_write correlated threats to mcp_threat_associations with threat_type=correlated_*. Write CORRELATION_REPORT.md. Heartbeat."},

{"n":"089","task":"build_remediation_advisor","file":"remediation_advisor.py","c":"medium","ph":"13","pri":0.58,
"reads":["schema.py","verdict_taxonomy.py"],
"desc":"Remediation advisor. get_remediation_steps(server_id)->list: fetches verdict+signals+threats. Returns ordered list of specific remediation steps based on findings. Examples: if tool_description_safety<40: Conduct manual review of tool descriptions for injection patterns. if supply_chain<30: Verify package provenance and check npm author identity. if rug_pull threat detected: Immediately revoke all active sessions using this MCP. Each step has priority (1-5), estimated_effort (hours), and responsible_team (security|developer|platform). generate_remediation_report(server_id)->str: full markdown remediation plan. No daemon."},

{"n":"090","task":"build_exemption_manager","file":"exemption_manager.py","c":"medium","ph":"13","pri":0.59,
"reads":["schema.py"],
"desc":"Exemption manager. Handles cases where an MCP is approved despite negative signals. grant_exemption(server_id,reason,granted_by,expires_days=30,conditions=[])->str: writes to mcp_exemptions table {server_id,reason,granted_by,expires_at,conditions_json,active=True}. check_exemption(server_id)->dict|None: returns active exemption if exists and not expired. revoke_exemption(server_id,revoked_by)->bool. list_expiring_exemptions(days=7)->list. Exemptions override policy_engine ESCALATE decisions to CONDITIONAL_ALLOW. Daily check emits warnings for expiring exemptions. No daemon."},

{"n":"091","task":"build_api_gateway","file":"api_gateway.py","c":"high","ph":"12","pri":0.64,
"reads":["schema.py","rate_limiter.py"],
"desc":"API gateway. FastAPI port 8787. Single entry point that proxies to all ZO-SENTINEL APIs. Routes: /assess/* -> registry_api:8781, /search/* -> search_api:8782, /submit/* -> approval_workflow:8780, /dashboard/* -> dashboard_api:8783, /bulk/* -> bulk_assess_api:8784, /compare/* -> comparison_api:8785. Adds rate limiting via RateLimiter middleware. Adds X-Sentinel-Version header. Adds request logging to mesh_events. GET /gateway/health checks all downstream /health endpoints. Daemon uvicorn port 8787. Heartbeat."},

{"n":"092","task":"build_sdk_client","file":"sentinel_sdk.py","c":"medium","ph":"12","pri":0.62,
"reads":["env_config.py"],
"desc":"Python SDK client for ZO-SENTINEL. SentinelClient class: __init__(base_url=http://localhost:8787). Methods: assess(mcp_name_or_url)->AssessmentResult, search(query,limit=10)->list, submit_for_review(name,url,description,requested_by)->str (job_id), get_status(job_id)->dict, is_trusted(mcp_name)->bool (quick check), get_risk_tier(mcp_name)->str. AssessmentResult dataclass: server_id,name,verdict,trust_score,risk_tier,attestation_summary,recommended_action. Importable by other tools: from sentinel_sdk import SentinelClient. No daemon."},

{"n":"093","task":"build_cli_main","file":"sentinel_cli.py","c":"medium","ph":"12","pri":0.61,
"reads":["sentinel_sdk.py","report_formatter.py"],
"desc":"Main CLI for ZO-SENTINEL. Uses argparse. Commands: assess <mcp> (full assessment with colour output), search <query>, submit <url> --name --description, status (pipeline health), risks --tier=CRITICAL, threats --days=7, report (generate compliance report), seed (run quick_seed.py), validate (run config_validator.py). Each command uses SentinelClient. Colour output using ANSI. --json flag for machine-readable output. Installed as: python3 sentinel_cli.py <command>. No daemon."},

{"n":"094","task":"build_graphql_schema","file":"graphql_schema.py","c":"high","ph":"15","pri":0.50,
"reads":["schema.py","registry_api.py"],
"desc":"GraphQL schema for ZO-SENTINEL using strawberry-graphql. Types: MCPServer(server_id,name,url,verdict,trust_score), SignalScore(signal_name,score,evidence), ThreatAssociation(threat_type,severity,evidence), Assessment(server,signals,threats,attestation). Query: server(id)->MCPServer, servers(verdict,risk_tier,limit)->list, search(q)->list, threats(severity)->list. FastAPI integration on port 8788 at /graphql. Daemon uvicorn. Heartbeat. Requires: pip install strawberry-graphql."},

{"n":"095","task":"build_metrics_exporter","file":"metrics_exporter.py","c":"medium","ph":"11","pri":0.60,
"reads":["schema.py"],
"desc":"Prometheus-compatible metrics exporter. FastAPI port 8789 GET /metrics returns text/plain prometheus format. Metrics: zo_sentinel_servers_total{verdict} gauge, zo_sentinel_trust_score_avg gauge, zo_sentinel_threats_total{severity} counter, zo_sentinel_assessments_24h counter, zo_sentinel_pipeline_health{check} gauge (1=ok 0=degraded), zo_sentinel_api_latency_ms{service} gauge. Updates every 60s via background task. GET /health. Daemon uvicorn port 8789. Heartbeat."},

{"n":"096","task":"build_watch_mode","file":"watch.py","c":"medium","ph":"10","pri":0.65,
"reads":["schema.py"],
"desc":"Watch mode CLI. python3 watch.py [--interval=30]. Clears terminal every interval seconds and prints live dashboard: (1) running daemon PIDs and status (2) last 5 build events from mesh_events (3) pipeline health counts (4) last 3 threats detected (5) assessment queue depth. Uses ANSI escape codes for colour and cursor positioning. Like htop but for ZO-SENTINEL. No daemon, runs interactively."},

{"n":"097","task":"build_context_injector","file":"context_injector.py","c":"medium","ph":"9","pri":0.67,
"reads":["schema.py"],
"desc":"Context injector for build prompts. Reads current ZO-SENTINEL state and injects it into MiniMax generation prompts. get_build_context()->str: queries mcp_server_registry count, lists built files with sizes, reads KNOWLEDGE_BASE.md, reads last 3 build failures from GENERATION_FAILURES.md, reads BUILD_STATE.md. Used by builder to enrich prompts with current system state. Caches context for 300s. No daemon, utility."},

{"n":"098","task":"build_directive_validator","file":"directive_validator.py","c":"low","ph":"9","pri":0.68,
"reads":[],
"desc":"Directive validator utility. validate_directive(d:dict)->tuple[bool,list]: checks required fields (task,handler,output_file,complexity,description), validates complexity in (high,medium,low), validates handler in (generate_file,write_raw,run_script), checks output_file ends in .py or known extensions, checks description length >50 chars, checks priority 0-1 range. Returns (valid,errors). scan_directive_folder(path)->dict: validates all JSON files in directives/ folder. Reports malformed directives. Run: python3 directive_validator.py. No daemon."},

{"n":"099","task":"build_mesh_bridge","file":"mesh_bridge.py","c":"high","ph":"9","pri":0.69,
"reads":["schema.py"],
"desc":"Mesh bridge for ZO-SENTINEL to ZOMesh integration. Reads mcp_server_registry verdict changes and emits GateObjects to ZOMesh RAW_OUTPUT topic. Functions: emit_verdict_gate(server_id,verdict,trust_score,reasoning)->bool: creates GateObject with payload_type=mcp_verdict_update and emits to mesh via requests POST to mesh_guardian API. subscribe_to_assessments(): polls mesh_events for assessment_requested events. Bridges ZO-SENTINEL assessments into the broader ZOMesh governance pipeline. Daemon polls 300s. Heartbeat."},

{"n":"100","task":"build_sentinel_supervisor","file":"supervisord_sentinel.conf","c":"low","ph":"10","pri":0.74,
"reads":[],
"desc":"Supervisord config for ZO-SENTINEL daemons. [program:sentinel_mcp_scanner] command=python3 /home/workspace/zo_sentinel/mcp_scanner.py, [program:sentinel_signal_analyser], [program:sentinel_trust_synthesiser], [program:sentinel_threat_intel], [program:sentinel_risk_ranker], [program:sentinel_attestation], [program:sentinel_approval_workflow] port 8780, [program:sentinel_registry_api] port 8781, [program:sentinel_search_api] port 8782, [program:sentinel_pipeline_health], [program:sentinel_alert_manager]. All: autostart=true autorestart=true redirect_stderr=true stdout_logfile=/home/workspace/logs/sentinel_%(program_name)s.log."},

]

# Write all directives
written = 0
for d in DIRECTIVES:
    n      = d.pop("n")
    task   = d["task"]
    fname  = f"{n}_{task}.json"
    fpath  = DIR / fname
    if fpath.exists():
        print(f"  SKIP (exists): {fname}")
        continue
    payload = {
        "task":        d["task"],
        "handler":     "generate_file",
        "output_file": d["file"],
        "complexity":  d["c"],
        "phase":       d["ph"],
        "priority":    d["pri"],
        "reads":       d.get("reads", []),
        "description": d["desc"],
        "context":     d.get("ctx", ""),
        "from":        "claude_batch",
    }
    fpath.write_text(json.dumps(payload, indent=2))
    written += 1

print(f"Written {written}/{len(DIRECTIVES)} directives to {DIR}")
print("Priority order: schema(0.99) > schema_runner(0.98) > quick_seed(0.96) > signal_analyser(0.95) ...")