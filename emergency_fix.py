#!/usr/bin/env python3
"""Emergency fix: recreate schema + kill duplicate builder + run architect dry run."""
import requests, subprocess, sys, json, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger()

WS = 'http://127.0.0.1:8772'

def ex(sql, label):
    try:
        r = requests.post(WS + '/execute', json={'sql': sql.strip(), 'wait': True}, timeout=15)
        if r.status_code == 200:
            log.info('[OK] %s', label)
            return True
        log.warning('[FAIL] %s: %s', label, r.text[:80])
    except Exception as e:
        log.error('[ERR] %s: %s', label, e)
    return False

# 1. Kill duplicate builder (keep the lower PID)
result = subprocess.run(['pgrep', '-f', 'zo_sentinel_builder'], capture_output=True, text=True)
pids = sorted([int(p) for p in result.stdout.strip().split() if p])
if len(pids) > 1:
    for pid in pids[1:]:  # kill all but the first
        subprocess.run(['kill', str(pid)])
        log.info('[OK] Killed duplicate builder PID %d', pid)
else:
    log.info('[OK] Builder PIDs: %s (no duplicates)', pids)

# 2. Recreate all sentinel tables
TABLES = [
    ("""CREATE TABLE IF NOT EXISTS mcp_server_registry (
        server_id VARCHAR PRIMARY KEY, name VARCHAR, registry_source VARCHAR,
        url VARCHAR, description TEXT, trust_score FLOAT, verdict VARCHAR,
        verdict_reasoning TEXT, confidence FLOAT, last_assessed TIMESTAMPTZ,
        first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ, last_scanned TIMESTAMPTZ,
        scan_count INTEGER DEFAULT 0, risk_tier VARCHAR, metadata TEXT
    )""", 'mcp_server_registry'),
    ("""CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        id BIGINT DEFAULT nextval('seq_signal_id'),
        server_id VARCHAR NOT NULL, signal_name VARCHAR, score FLOAT,
        evidence TEXT, scored_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_signal_scores'),
    ("""CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id BIGINT DEFAULT nextval('seq_threat_id'),
        server_id VARCHAR NOT NULL, threat_type VARCHAR, evidence TEXT,
        severity VARCHAR, reported_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_threat_associations'),
    ("""CREATE TABLE IF NOT EXISTS mcp_risk_register (
        server_id VARCHAR PRIMARY KEY, name VARCHAR, risk_rank FLOAT,
        risk_tier VARCHAR, threat_count INTEGER DEFAULT 0,
        staleness_days INTEGER DEFAULT 0,
        environment_exposure VARCHAR DEFAULT 'unknown',
        computed_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_risk_register'),
    ("""CREATE TABLE IF NOT EXISTS mcp_attestations (
        attestation_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        server_id VARCHAR NOT NULL, attestation_text TEXT, scope VARCHAR,
        confidence_level FLOAT, valid_until TIMESTAMPTZ, risk_tier VARCHAR,
        caveats TEXT, status VARCHAR DEFAULT 'active',
        generated_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_attestations'),
    ("""CREATE TABLE IF NOT EXISTS mcp_submissions (
        submission_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        server_id VARCHAR, mcp_name VARCHAR NOT NULL, url VARCHAR,
        description TEXT, requested_by VARCHAR, business_purpose TEXT,
        environment VARCHAR, submitted_at TIMESTAMPTZ DEFAULT now(),
        status VARCHAR DEFAULT 'pending'
    )""", 'mcp_submissions'),
    ("""CREATE TABLE IF NOT EXISTS mcp_decisions (
        id BIGINT DEFAULT nextval('seq_decision_id'),
        submission_id VARCHAR, analyst_name VARCHAR, decision VARCHAR,
        conditions TEXT, notes TEXT, expiry_days INTEGER DEFAULT 90,
        expires_at TIMESTAMPTZ, decided_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_decisions'),
    ("""CREATE TABLE IF NOT EXISTS mcp_policy_rules (
        id BIGINT DEFAULT nextval('seq_policy_id'),
        rule_name VARCHAR UNIQUE NOT NULL, rule_type VARCHAR,
        pattern VARCHAR, action VARCHAR, description TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_policy_rules'),
    ("""CREATE TABLE IF NOT EXISTS mcp_definition_history (
        id BIGINT DEFAULT nextval('seq_defhist_id'),
        server_id VARCHAR, snapshot_hash VARCHAR,
        captured_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_definition_history'),
    ("""CREATE TABLE IF NOT EXISTS audit_log (
        event_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        event_type VARCHAR, actor VARCHAR, target_server_id VARCHAR,
        action VARCHAR, outcome VARCHAR, details_json TEXT,
        immutable BOOLEAN DEFAULT TRUE, timestamp TIMESTAMPTZ DEFAULT now()
    )""", 'audit_log'),
    ("""CREATE TABLE IF NOT EXISTS auth_tokens (
        token_id VARCHAR PRIMARY KEY, action VARCHAR, mcp_name VARCHAR,
        submission_id VARCHAR, requested_by VARCHAR, admin_email VARCHAR,
        expires_at TIMESTAMPTZ, used BOOLEAN DEFAULT FALSE,
        used_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now()
    )""", 'auth_tokens'),
    ("""CREATE TABLE IF NOT EXISTS mcp_risk_register (
        server_id VARCHAR PRIMARY KEY, name VARCHAR, risk_rank FLOAT,
        risk_tier VARCHAR, threat_count INTEGER DEFAULT 0,
        staleness_days INTEGER DEFAULT 0, computed_at TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_risk_register (idempotent)'),
    ("""CREATE TABLE IF NOT EXISTS shodan_results (
        server_id VARCHAR, ip_address VARCHAR, open_ports TEXT,
        cves_found TEXT, exposure_score FLOAT, scanned_at TIMESTAMPTZ DEFAULT now()
    )""", 'shodan_results'),
    ("""CREATE TABLE IF NOT EXISTS github_velocity (
        server_id VARCHAR, repo_url VARCHAR, commit_velocity FLOAT,
        contributor_churn FLOAT, last_suspicious_commit VARCHAR,
        checked_at TIMESTAMPTZ DEFAULT now()
    )""", 'github_velocity'),
    ("""CREATE TABLE IF NOT EXISTS npm_typosquat_alerts (
        suspect_name VARCHAR, target_name VARCHAR, levenshtein_dist INTEGER,
        npm_downloads INTEGER DEFAULT 0, published_at TIMESTAMPTZ,
        flagged_at TIMESTAMPTZ DEFAULT now()
    )""", 'npm_typosquat_alerts'),
]

# Create sequences first
for seq in ['seq_signal_id','seq_threat_id','seq_decision_id','seq_policy_id',
            'seq_defhist_id','seq_id']:
    ex(f'CREATE SEQUENCE IF NOT EXISTS {seq} START 1', f'sequence {seq}')

created = sum(1 for sql, label in TABLES if ex(sql, label))
log.info('Schema: %d/%d tables created', created, len(TABLES))

# 3. Run architect dry run and LOG it
log.info('')
log.info('=== ARCHITECT DRY RUN ===')
try:
    sys.path.insert(0, '/home/workspace/zo_sentinel')
    import enterprise_architect_loop as arch
    import logging as _l
    _l.basicConfig(level=_l.INFO)
    result = arch.evaluate_and_queue(max_directives=3, dry_run=True)
    log.info('Architect result: %s', json.dumps(result, indent=2))
except Exception as e:
    log.error('Architect error: %s', e)
    import traceback; traceback.print_exc()

log.info('')
log.info('Done. Scanner will retry on next cycle (6h). Builder is running.')