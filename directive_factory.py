#!/usr/bin/env python3
"""
directive_factory.py v2 -- Idempotent precision directive generator.

Deduplication layers:
  1. Scans all *.json AND *.done.json in directives/ for existing task names
  2. Queries mesh_memory build_artifact records for already-built tasks
  3. Only generates net-new directives

Usage: python3 /home/workspace/zo_sentinel/directive_factory.py
To add more directives: append to NEW_DIRECTIVES list and re-run.
"""
import os, json, glob, requests

QUEUE_DIR = "/home/workspace/zo_sentinel/directives"
WRITE_SERVICE = "http://127.0.0.1:8772"


def get_existing_task_names(queue_dir):
    """
    Layer 1: Parse ALL directive files (pending + done) for task names.
    Catches: already-queued, already-completed, and duplicate submissions.
    """
    tasks = set()
    # Both *.json and *.done.json
    for fpath in glob.glob(os.path.join(queue_dir, '*.json')):
        try:
            with open(fpath) as f:
                d = json.load(f)
            if 'task' in d:
                tasks.add(d['task'])
        except Exception:
            print('  WARN: unreadable', os.path.basename(fpath))
    return tasks


def get_built_task_names():
    """
    Layer 2: Query mesh_memory for build_artifact records.
    Catches tasks that completed and whose directive file may be gone.
    """
    tasks = set()
    try:
        r = requests.post(WRITE_SERVICE + '/query',
            json={'sql': "SELECT content FROM mesh_memory WHERE "
                          "agent_id='t1.zo_sentinel_builder' AND "
                          "memory_type='build_artifact'"},
            timeout=8)
        if r.status_code == 200:
            for row in r.json().get('rows', []):
                try:
                    c = json.loads(row.get('content', '{}'))
                    if 'task' in c:
                        tasks.add(c['task'])
                except Exception:
                    pass
    except Exception as e:
        print('  WARN: mesh_memory query failed:', e)
    return tasks


def get_next_seq(queue_dir, start=106):
    """Auto-increment from highest existing sequence number."""
    max_num = start
    for fpath in glob.glob(os.path.join(queue_dir, '*.json')):
        try:
            num = int(os.path.basename(fpath).split('_')[0])
            if num > max_num:
                max_num = num
        except ValueError:
            pass
    return max_num + 1


# ── Directives 107-120: Advanced threat intelligence ──────────────────────
# Add new entries here and re-run. Script is idempotent.
# REQUIRED fields: task, output_file, complexity, phase, priority, description
# complexity must be 'high'|'medium'|'low' (controls MiniMax vs Ollama routing)
# handler is always 'generate_file' -- never a model name

NEW_DIRECTIVES = [
    {
        "task": "build_prompt_injection_scanner",
        "output_file": "prompt_injection_scanner.py",
        "complexity": "high", "phase": "16", "priority": 0.96,
        "reads": ["schema.py", "known_threats.py", "text_patterns.py"],
        "description": (
            "Passive prompt injection scanner daemon. Reads tool descriptions from "
            "mcp_server_registry via ws_query. For each server: scan description and "
            "fetched tool schema (GET {url}/tools timeout=5s) for injection patterns: "
            "(1) hidden XML/HTML tags in descriptions, "
            "(2) system prompt override attempts: ignore previous, disregard, act as, "
            "(3) invisible unicode: zero-width space U+200B, soft hyphen U+00AD, "
            "(4) base64-encoded instruction blobs in description fields, "
            "(5) nested tool calls attempting to invoke other MCPs. "
            "Risk: 1+ pattern = trust_score -= 30, severity=CRITICAL. "
            "ws_write mcp_threat_associations {server_id, threat_type=prompt_injection, "
            "evidence=pattern+location, severity=CRITICAL}. "
            "ws_write mcp_signal_scores tool_description_safety update. Poll 14400s. Heartbeat."
        )
    },
    {
        "task": "build_sybil_burst_detector",
        "output_file": "sybil_burst_detector.py",
        "complexity": "high", "phase": "16", "priority": 0.94,
        "reads": ["schema.py", "db_utils.py"],
        "description": (
            "Sybil and burst request detector daemon. Reads mesh_events WHERE "
            "event_type='assessment_requested' AND created_at > now()-1h via ws_query. "
            "Groups by json_extract_string(payload,'$.server_id') and date_trunc('minute',created_at). "
            "Detects: >20 assessment requests for same server_id in 60s = burst_attack. "
            "Also: 10+ NEW server_ids registered within 5 minutes from same registry_source "
            "= coordinated_registration (Sybil seeding). "
            "Check identical description text: ws_query mcp_server_registry self-join on TRIM(description). "
            "ws_write mcp_threat_associations {threat_type=sybil_burst or coordinated_registration, "
            "severity=HIGH, evidence=count+window}. Poll 300s. Heartbeat."
        )
    },
    {
        "task": "build_context_manipulation_detector",
        "output_file": "context_manipulation_detector.py",
        "complexity": "high", "phase": "16", "priority": 0.95,
        "reads": ["schema.py", "text_patterns.py"],
        "description": (
            "Context manipulation detector daemon. For each MCP in mcp_server_registry: "
            "fetch tool JSON schema GET {url}/tools 5s timeout. Parse each tool inputSchema.properties. "
            "Dangerous patterns: "
            "(1) parameters named: prompt, instruction, system, context, override "
            "(2) string params with no maxLength (unbounded injection surface) "
            "(3) additionalProperties: true on any schema "
            "(4) tools with >10 required parameters "
            "Blast radius: (file_path param AND url param in same tool) = exfil_combo = CRITICAL. "
            "(command OR shell param) = code_exec = CRITICAL. "
            "ws_write mcp_threat_associations for CRITICAL/HIGH findings. "
            "ws_write mcp_signal_scores permission_scope update. Poll 21600s. Heartbeat."
        )
    },
    {
        "task": "build_trust_score_time_series",
        "output_file": "trust_score_time_series.py",
        "complexity": "medium", "phase": "16", "priority": 0.88,
        "reads": ["schema.py"],
        "description": (
            "Trust score drift detector daemon. Every 21600s: compare current trust_score "
            "in mcp_server_registry to score from 7 days ago via mcp_signal_scores history. "
            "Thresholds: delta > +20 (rapid improvement = possible manipulation) = WARNING, "
            "delta < -15 = HIGH, trust_score cliff drop from >60 to <35 = CRITICAL. "
            "ws_write mcp_threat_associations for CRITICAL drift. "
            "ws_write mesh_events {event_type=trust_score_drift, "
            "payload={server_id,old_score,new_score,delta}}. "
            "Write DRIFT_REPORT.md sorted by abs(delta) desc. Heartbeat."
        )
    },
    {
        "task": "build_vendor_concentration_monitor",
        "output_file": "vendor_concentration_monitor.py",
        "complexity": "medium", "phase": "16", "priority": 0.85,
        "reads": ["schema.py"],
        "description": (
            "Vendor concentration risk monitor. Groups mcp_server_registry by npm scope, "
            "GitHub org, and domain. Flags: single vendor >5 approved servers = WARNING, "
            ">40% of all approved servers from one vendor = HIGH (SPOF risk). "
            "ws_write mesh_events {event_type=vendor_concentration_alert, "
            "payload={vendor,server_count,percentage}}. "
            "Write VENDOR_REPORT.md. Poll 43200s. Heartbeat."
        )
    },
    {
        "task": "build_approval_anomaly_detector",
        "output_file": "approval_anomaly_detector.py",
        "complexity": "medium", "phase": "16", "priority": 0.86,
        "reads": ["schema.py"],
        "description": (
            "Approval workflow anomaly detector. Reads mcp_decisions via ws_query. "
            "Detects: analyst approving >90% of submissions = rubber_stamp, "
            "decision made <2min after submission = suspicious_speed, "
            "approved server trust_score < 30 = anomalous_approval, "
            ">5 ALLOW decisions same analyst in 1hr = bulk_approval_burst, "
            "requested_by == analyst_name = conflict_of_interest. "
            "ws_write corrections {agent_id=zo_sentinel.approval_auditor, "
            "action=approval_anomaly, reason=desc, cluster=governance}. "
            "ws_write mesh_events severity=WARNING. Poll 3600s. Heartbeat."
        )
    },
    {
        "task": "build_certificate_analyser",
        "output_file": "certificate_analyser.py",
        "complexity": "medium", "phase": "16", "priority": 0.84,
        "reads": ["schema.py", "url_analyser.py"],
        "description": (
            "TLS certificate analyser daemon. For each HTTPS server: "
            "ssl.get_server_certificate((host, 443)) via socket. "
            "Risk: self_signed = -30 domain_trust, expired cert = -40, "
            "cert valid <7d = -20, hostname mismatch = CRITICAL, "
            "unknown CA = -25. "
            "ws_write mcp_signal_scores domain_trust update. "
            "ws_write mcp_threat_associations for CRITICAL. Poll 43200s. Heartbeat."
        )
    },
    {
        "task": "build_dependency_chain_auditor",
        "output_file": "dependency_chain_auditor.py",
        "complexity": "high", "phase": "16", "priority": 0.91,
        "reads": ["schema.py", "known_threats.py"],
        "description": (
            "npm dependency chain auditor. For npm MCPs: fetch package.json from "
            "https://registry.npmjs.org/{name}/latest. Check dependencies: "
            "(1) against KNOWN_MALICIOUS_PACKAGES, "
            "(2) OSV.dev CVEs: POST https://api.osv.dev/v1/query, "
            "(3) 0-download transitive deps = dependency confusion, "
            "(4) git:// URL pins = mutable supply chain risk, "
            "(5) file:// paths = local injection. "
            "2 levels deep. ws_write mcp_threat_associations per finding. "
            "ws_write mcp_signal_scores supply_chain update. Poll 86400s. Heartbeat."
        )
    },
    {
        "task": "build_mcp_impersonation_detector",
        "output_file": "mcp_impersonation_detector.py",
        "complexity": "high", "phase": "16", "priority": 0.92,
        "reads": ["schema.py", "similarity_scorer.py", "known_threats.py"],
        "description": (
            "MCP impersonation detector. Builds canonical name index from mcp_server_registry. "
            "Detects: namespace squatting (missing @scope), homoglyph attacks (o->0 l->1 rn->m), "
            "legitimacy suffix injection (mcp-postgres-official vs mcp-postgres), "
            "reversed scope (server-filesystem-mcp vs mcp-server-filesystem), "
            "hyphenation variants. similarity_scorer.name_similarity() for each pair. "
            "If confidence > 70 AND suspect has fewer downloads: "
            "ws_write mcp_threat_associations {threat_type=impersonation_attempt, "
            "severity=HIGH, evidence=suspect_name+target+score}. Poll 86400s. Heartbeat."
        )
    },
    {
        "task": "build_threat_feed_aggregator",
        "output_file": "threat_feed_aggregator.py",
        "complexity": "high", "phase": "16", "priority": 0.90,
        "reads": ["schema.py", "known_threats.py"],
        "description": (
            "Threat feed aggregator. Polls: "
            "(1) CISA KEV: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json, "
            "(2) URLhaus: https://urlhaus-api.abuse.ch/v1/urls/recent/, "
            "(3) OpenPhish: https://openphish.com/feed.txt, "
            "(4) world_articles WHERE topics LIKE '%mcp%' AND (title ILIKE '%malicious%' OR '%supply chain%'). "
            "Cross-reference each against mcp_server_registry URLs/IPs via ws_query. "
            "On match: ws_write mcp_threat_associations severity=CRITICAL. "
            "Set verdict=KNOWN_THREAT via ws_write if CRITICAL match. Poll 3600s. Heartbeat."
        )
    },
    {
        "task": "build_cross_registry_correlator",
        "output_file": "cross_registry_correlator.py",
        "complexity": "high", "phase": "16", "priority": 0.89,
        "reads": ["schema.py", "known_threats.py"],
        "description": (
            "Cross-registry threat correlator. For each server in mcp_server_registry: "
            "(1) PhishTank: POST https://checkurl.phishtank.com/checkurl/ with url field, "
            "(2) AbuseIPDB: GET https://api.abuseipdb.com/api/v2/check?ipAddress={ip} with key header if ABUSEIPDB_KEY set, "
            "(3) Maltiverse: GET https://api.maltiverse.com/hostname/{host} if MALTIVERSE_KEY set. "
            "All APIs: graceful skip if key not set. Rate limit: time.sleep(1). "
            "Positive hit: ws_write mcp_threat_associations severity=CRITICAL, "
            "set verdict=KNOWN_THREAT. Poll 86400s. Heartbeat."
        )
    },
    {
        "task": "build_runtime_behaviour_profiler",
        "output_file": "runtime_behaviour_profiler.py",
        "complexity": "high", "phase": "16", "priority": 0.88,
        "reads": ["schema.py", "mesh_bridge.py"],
        "description": (
            "Runtime behaviour profiler. Reads mesh_events WHERE "
            "event_type IN ('assessment_requested','mcp_verdict_update') "
            "AND created_at > now()-24h via ws_query. "
            "Profile per server_id: call_frequency (calls/hr), unique_callers, "
            "time_of_day_distribution, payload_size_variance. "
            "Anomalies: >100 calls/hr = rate_abuse, "
            ">90% calls between 0-6 UTC = evasion_pattern, "
            "payload >10x median = bulk_exfiltration. "
            "ws_write mcp_threat_associations per anomaly. "
            "ws_write mcp_signal_scores temporal_stability update. "
            "Write BEHAVIOUR_PROFILE.md. Poll 3600s. Heartbeat."
        )
    },
    {
        "task": "build_mcp_age_risk_scorer",
        "output_file": "mcp_age_risk_scorer.py",
        "complexity": "medium", "phase": "16", "priority": 0.87,
        "reads": ["schema.py"],
        "description": (
            "Package age risk scorer. For npm MCPs: GET https://registry.npmjs.org/{name}. "
            "Parse time.created. age_days < 7 = -25 temporal_stability, "
            "< 30 = -15, 30-90 = -5, > 365 = +10. "
            "age_days < 30 AND downloads < 10 = CAUTION_LIMITED floor. "
            "age_days < 7 AND any threat = HIGH_RISK_ISOLATED floor. "
            "ws_write mcp_signal_scores temporal_stability. Poll 86400s. Heartbeat."
        )
    },
    {
        "task": "build_tool_schema_deep_scanner",
        "output_file": "tool_schema_deep_scanner.py",
        "complexity": "high", "phase": "16", "priority": 0.93,
        "reads": ["schema.py", "known_threats.py"],
        "description": (
            "Deep tool schema scanner. For each server: GET {url}/tools 5s timeout. "
            "(1) additionalProperties:true = accepts arbitrary input = MEDIUM, "
            "(2) missing maxLength on string params = injection surface = MEDIUM, "
            "(3) $ref circular schema = DoS risk = HIGH, "
            "(4) URL param + user input = SSRF_risk = HIGH, "
            "(5) default values containing IPs or internal hostnames = internal_probe = CRITICAL. "
            "ws_write mcp_threat_associations per finding. "
            "ws_write mcp_signal_scores tool_description_safety and permission_scope. "
            "Write SCHEMA_SCAN_REPORT.md. Poll 21600s. Heartbeat."
        )
    },
]


def generate_directives():
    os.makedirs(QUEUE_DIR, exist_ok=True)

    # Deduplication: layer 1 (files) + layer 2 (mesh_memory)
    file_tasks  = get_existing_task_names(QUEUE_DIR)
    built_tasks = get_built_task_names()
    all_known   = file_tasks | built_tasks
    print(f"Dedup index: {len(file_tasks)} queued + {len(built_tasks)} built = {len(all_known)} known tasks")

    seq     = get_next_seq(QUEUE_DIR)
    created = 0
    skipped = 0

    for d in NEW_DIRECTIVES:
        task = d['task']
        if task in all_known:
            print(f"  SKIP (exists): {task}")
            skipped += 1
            continue

        fname = str(seq).zfill(3) + '_' + task + '.json'
        fpath = os.path.join(QUEUE_DIR, fname)
        payload = {
            "task":        task,
            "handler":     "generate_file",   # always generate_file, never a model name
            "output_file": d['output_file'],
            "complexity":  d.get('complexity', 'high'),
            "phase":       d.get('phase', '16'),
            "priority":    float(d.get('priority', 0.88)),
            "reads":       d.get('reads', ['schema.py']),
            "description": d['description'],
            "context":     d.get('context', ''),
            "from":        "directive_factory_v2"
        }
        with open(fpath, 'w') as f:
            json.dump(payload, f, indent=2)
        print(f"  [OK] {fname} ({payload['complexity']} p={payload['priority']})")
        all_known.add(task)  # update dedup index for this run
        seq += 1
        created += 1

    print(f"\nDone: {created} created, {skipped} skipped. Run again safely anytime.")


if __name__ == '__main__':
    generate_directives()