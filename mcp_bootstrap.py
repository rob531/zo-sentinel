#!/usr/bin/env python3
"""Run by MCP to fix schema without blocking terminal."""
import requests, time, subprocess, os

# Kill anything blocking
for name in ['full_schema_bootstrap', 'bootstrap_missing', 'zm go', 'go.sh']:
    subprocess.run(['pkill', '-f', name], capture_output=True)
time.sleep(2)

WS = 'http://127.0.0.1:8772'

def ex(sql, label):
    try:
        r = requests.post(WS + '/execute', json={'sql': sql.strip(), 'wait': True}, timeout=15)
        status = 'OK' if r.status_code == 200 else f'FAIL {r.status_code}'
        print(f'[{status}] {label}')
        return r.status_code == 200
    except Exception as e:
        print(f'[ERR] {label}: {e}')
        return False

# Check write_service
try:
    r = requests.get(WS + '/health', timeout=5)
    print(f'write_service: HTTP {r.status_code}')
except Exception as e:
    print(f'write_service: UNREACHABLE {e}')
    exit(1)

ex('CREATE SEQUENCE IF NOT EXISTS seq_signal_id START 1', 'seq_signal_id')
ex('CREATE SEQUENCE IF NOT EXISTS seq_threat_id START 1', 'seq_threat_id')
ex('CREATE SEQUENCE IF NOT EXISTS seq_decision_id START 1', 'seq_decision_id')
ex('CREATE SEQUENCE IF NOT EXISTS seq_policy_id START 1', 'seq_policy_id')
ex('CREATE SEQUENCE IF NOT EXISTS seq_defhist_id START 1', 'seq_defhist_id')
ex('CREATE SEQUENCE IF NOT EXISTS seq_id START 1', 'seq_id')

ex("""CREATE TABLE IF NOT EXISTS mcp_server_registry (
    server_id VARCHAR PRIMARY KEY, name VARCHAR, registry_source VARCHAR,
    url VARCHAR, description TEXT, trust_score FLOAT, verdict VARCHAR,
    verdict_reasoning TEXT, confidence FLOAT, last_assessed TIMESTAMPTZ,
    first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ, last_scanned TIMESTAMPTZ,
    scan_count INTEGER DEFAULT 0, risk_tier VARCHAR, metadata TEXT
)""", 'mcp_server_registry')

ex("""CREATE TABLE IF NOT EXISTS mcp_signal_scores (
    id BIGINT DEFAULT nextval('seq_signal_id'),
    server_id VARCHAR NOT NULL, signal_name VARCHAR, score FLOAT,
    evidence TEXT, scored_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_signal_scores')

ex("""CREATE TABLE IF NOT EXISTS mcp_threat_associations (
    id BIGINT DEFAULT nextval('seq_threat_id'),
    server_id VARCHAR NOT NULL, threat_type VARCHAR, evidence TEXT,
    severity VARCHAR, reported_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_threat_associations')

ex("""CREATE TABLE IF NOT EXISTS mcp_risk_register (
    server_id VARCHAR PRIMARY KEY, name VARCHAR, risk_rank FLOAT,
    risk_tier VARCHAR, threat_count INTEGER DEFAULT 0,
    staleness_days INTEGER DEFAULT 0, computed_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_risk_register')

ex("""CREATE TABLE IF NOT EXISTS mcp_attestations (
    attestation_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
    server_id VARCHAR NOT NULL, attestation_text TEXT, scope VARCHAR,
    confidence_level FLOAT, valid_until TIMESTAMPTZ, risk_tier VARCHAR,
    caveats TEXT, status VARCHAR DEFAULT 'active', generated_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_attestations')

ex("""CREATE TABLE IF NOT EXISTS mcp_submissions (
    submission_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
    server_id VARCHAR, mcp_name VARCHAR NOT NULL, url VARCHAR,
    description TEXT, requested_by VARCHAR, business_purpose TEXT,
    environment VARCHAR, submitted_at TIMESTAMPTZ DEFAULT now(), status VARCHAR DEFAULT 'pending'
)""", 'mcp_submissions')

ex("""CREATE TABLE IF NOT EXISTS mcp_decisions (
    id BIGINT DEFAULT nextval('seq_decision_id'),
    submission_id VARCHAR, analyst_name VARCHAR, decision VARCHAR,
    conditions TEXT, notes TEXT, expiry_days INTEGER DEFAULT 90,
    expires_at TIMESTAMPTZ, decided_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_decisions')

ex("""CREATE TABLE IF NOT EXISTS mcp_policy_rules (
    id BIGINT DEFAULT nextval('seq_policy_id'),
    rule_name VARCHAR UNIQUE NOT NULL, rule_type VARCHAR,
    pattern VARCHAR, action VARCHAR, description TEXT, created_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_policy_rules')

ex("""CREATE TABLE IF NOT EXISTS mcp_definition_history (
    id BIGINT DEFAULT nextval('seq_defhist_id'),
    server_id VARCHAR, snapshot_hash VARCHAR, captured_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_definition_history')

ex("""CREATE TABLE IF NOT EXISTS audit_log (
    event_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
    event_type VARCHAR, actor VARCHAR, target_server_id VARCHAR,
    action VARCHAR, outcome VARCHAR, details_json TEXT,
    immutable BOOLEAN DEFAULT TRUE, timestamp TIMESTAMPTZ DEFAULT now()
)""", 'audit_log')

ex("""CREATE TABLE IF NOT EXISTS auth_tokens (
    token_id VARCHAR PRIMARY KEY, action VARCHAR, mcp_name VARCHAR,
    submission_id VARCHAR, requested_by VARCHAR, admin_email VARCHAR,
    expires_at TIMESTAMPTZ, used BOOLEAN DEFAULT FALSE,
    used_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now()
)""", 'auth_tokens')

ex("""CREATE TABLE IF NOT EXISTS mcp_tool_hashes (
    server_id VARCHAR PRIMARY KEY, tools_hash VARCHAR, tools_raw TEXT,
    last_checked TIMESTAMPTZ DEFAULT now(), change_count INTEGER DEFAULT 0
)""", 'mcp_tool_hashes')

ex("""CREATE TABLE IF NOT EXISTS mcp_fingerprints (
    server_id VARCHAR PRIMARY KEY, tool_name_hash VARCHAR,
    description_tokens TEXT, permission_scope_hash VARCHAR,
    domain_fingerprint VARCHAR, version_string VARCHAR, computed_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_fingerprints')

ex("""CREATE TABLE IF NOT EXISTS mcp_exemptions (
    exemption_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
    server_id VARCHAR NOT NULL, reason TEXT, granted_by VARCHAR,
    conditions_json TEXT, expires_at TIMESTAMPTZ, active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now()
)""", 'mcp_exemptions')

ex("""CREATE TABLE IF NOT EXISTS perf_metrics (
    service VARCHAR, latency_ms FLOAT, recorded_at TIMESTAMPTZ DEFAULT now()
)""", 'perf_metrics')

ex("""CREATE TABLE IF NOT EXISTS shodan_results (
    server_id VARCHAR, ip_address VARCHAR, open_ports TEXT,
    cves_found TEXT, exposure_score FLOAT, scanned_at TIMESTAMPTZ DEFAULT now()
)""", 'shodan_results')

ex("""CREATE TABLE IF NOT EXISTS github_velocity (
    server_id VARCHAR, repo_url VARCHAR, commit_velocity FLOAT,
    contributor_churn FLOAT, last_suspicious_commit VARCHAR,
    checked_at TIMESTAMPTZ DEFAULT now()
)""", 'github_velocity')

ex("""CREATE TABLE IF NOT EXISTS npm_typosquat_alerts (
    id BIGINT DEFAULT nextval('seq_id'),
    suspect_name VARCHAR, target_name VARCHAR, levenshtein_dist INTEGER,
    npm_downloads INTEGER DEFAULT 0, published_at TIMESTAMPTZ,
    flagged_at TIMESTAMPTZ DEFAULT now()
)""", 'npm_typosquat_alerts')

# Verify
try:
    r = requests.post(WS + '/query',
        json={'sql': 'SELECT COUNT(*) as n FROM mcp_server_registry'}, timeout=10)
    n = r.json().get('rows', [{}])[0].get('n', '?')
    print(f'VERIFIED: mcp_server_registry exists with {n} rows')
except Exception as e:
    print(f'VERIFY ERROR: {e}')

print('DONE')