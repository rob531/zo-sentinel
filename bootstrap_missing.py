#!/usr/bin/env python3
"""Create all missing ZO-SENTINEL tables and seed quick_seed.py data.
   Run: python3 /home/workspace/zo_sentinel/bootstrap_missing.py
"""
import requests, logging, sys
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger('bootstrap')

EX = 'http://127.0.0.1:8772/execute'

def ex(sql, label):
    try:
        r = requests.post(EX, json={'sql': sql.strip(), 'wait': True}, timeout=10)
        if r.status_code == 200:
            log.info('[OK] %s', label)
            return True
        log.warning('[FAIL] %s: HTTP %s', label, r.status_code)
    except Exception as e:
        log.error('[ERR] %s: %s', label, e)
    return False

TABLES = [
    # -- schema_v2 tables (may have been skipped) --
    ("""CREATE TABLE IF NOT EXISTS mcp_submissions (
        submission_id  VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        server_id      VARCHAR,
        mcp_name       VARCHAR NOT NULL,
        url            VARCHAR,
        description    TEXT,
        requested_by   VARCHAR,
        business_purpose TEXT,
        environment    VARCHAR,
        submitted_at   TIMESTAMPTZ DEFAULT now(),
        status         VARCHAR DEFAULT 'pending'
    )""", 'mcp_submissions'),

    # -- Phase 7 --
    ("""CREATE TABLE IF NOT EXISTS mcp_risk_register (
        server_id       VARCHAR PRIMARY KEY,
        name            VARCHAR,
        risk_rank       FLOAT,
        risk_tier       VARCHAR,
        threat_count    INTEGER DEFAULT 0,
        staleness_days  INTEGER DEFAULT 0,
        environment_exposure VARCHAR DEFAULT 'unknown',
        computed_at     TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_risk_register'),

    ("""CREATE TABLE IF NOT EXISTS mcp_attestations (
        attestation_id  VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        server_id       VARCHAR NOT NULL,
        attestation_text TEXT,
        scope           VARCHAR,
        confidence_level FLOAT,
        valid_until     TIMESTAMPTZ,
        risk_tier       VARCHAR,
        caveats         TEXT,
        status          VARCHAR DEFAULT 'active',
        generated_at    TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_attestations'),

    # -- Phase 5 --
    ("""CREATE TABLE IF NOT EXISTS mcp_tool_hashes (
        server_id       VARCHAR PRIMARY KEY,
        tools_hash      VARCHAR,
        tools_raw       TEXT,
        last_checked    TIMESTAMPTZ DEFAULT now(),
        change_count    INTEGER DEFAULT 0
    )""", 'mcp_tool_hashes'),

    ("""CREATE TABLE IF NOT EXISTS mcp_fingerprints (
        server_id         VARCHAR PRIMARY KEY,
        tool_name_hash    VARCHAR,
        description_tokens TEXT,
        permission_scope_hash VARCHAR,
        domain_fingerprint VARCHAR,
        version_string    VARCHAR,
        computed_at       TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_fingerprints'),

    # -- Phase 13 --
    ("""CREATE TABLE IF NOT EXISTS audit_log (
        event_id        VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        event_type      VARCHAR,
        actor           VARCHAR,
        target_server_id VARCHAR,
        action          VARCHAR,
        outcome         VARCHAR,
        details_json    TEXT,
        immutable       BOOLEAN DEFAULT TRUE,
        timestamp       TIMESTAMPTZ DEFAULT now()
    )""", 'audit_log'),

    ("""CREATE TABLE IF NOT EXISTS mcp_exemptions (
        exemption_id    VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        server_id       VARCHAR NOT NULL,
        reason          TEXT,
        granted_by      VARCHAR,
        conditions_json TEXT,
        expires_at      TIMESTAMPTZ,
        active          BOOLEAN DEFAULT TRUE,
        created_at      TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_exemptions'),

    # -- Phase 11 --
    ("""CREATE TABLE IF NOT EXISTS perf_metrics (
        service         VARCHAR,
        latency_ms      FLOAT,
        recorded_at     TIMESTAMPTZ DEFAULT now()
    )""", 'perf_metrics'),

    # -- Phase 14 --
    ("""CREATE TABLE IF NOT EXISTS shodan_results (
        server_id       VARCHAR,
        ip_address      VARCHAR,
        open_ports      TEXT,
        cves_found      TEXT,
        exposure_score  FLOAT,
        scanned_at      TIMESTAMPTZ DEFAULT now()
    )""", 'shodan_results'),

    ("""CREATE TABLE IF NOT EXISTS github_velocity (
        server_id       VARCHAR,
        repo_url        VARCHAR,
        commit_velocity FLOAT,
        contributor_churn FLOAT,
        last_suspicious_commit VARCHAR,
        checked_at      TIMESTAMPTZ DEFAULT now()
    )""", 'github_velocity'),

    ("""CREATE TABLE IF NOT EXISTS npm_typosquat_alerts (
        id              BIGINT DEFAULT nextval('seq_id'),
        suspect_name    VARCHAR,
        target_name     VARCHAR,
        levenshtein_dist INTEGER,
        npm_downloads   INTEGER DEFAULT 0,
        published_at    TIMESTAMPTZ,
        flagged_at      TIMESTAMPTZ DEFAULT now()
    )""", 'npm_typosquat_alerts'),
]

# Sequence for npm alerts
ex("CREATE SEQUENCE IF NOT EXISTS seq_id START 1", 'sequence seq_id')

created = sum(1 for sql, label in TABLES if ex(sql, label))
log.info('Bootstrap complete: %d/%d tables created/verified', created, len(TABLES))

# Run schema_v2 for policy seeding
try:
    sys.path.insert(0, '/home/workspace/zo_sentinel')
    import schema_v2
    schema_v2.create_v2()
    schema_v2.seed_default_policies()
    log.info('[OK] schema_v2 complete + policies seeded')
except Exception as e:
    log.warning('schema_v2 import: %s', e)

# Run quick_seed to load real MCP data
try:
    import quick_seed
    log.info('[OK] quick_seed complete')
except Exception as e:
    log.warning('quick_seed: %s -- run manually if needed', e)

log.info('Done. Registry is ready for daemons.')