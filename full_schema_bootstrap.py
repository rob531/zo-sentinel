#!/usr/bin/env python3
"""
full_schema_bootstrap.py -- Creates ALL ZO-SENTINEL tables from scratch.
Safe to re-run -- all statements use CREATE TABLE IF NOT EXISTS.
Longer timeouts (120s) for post-reboot write_service warm-up.

HISTORY:
  2026-04-17: Added PRIMARY KEY and UNIQUE constraints to mcp_signal_scores
              and mcp_threat_associations. Previously these were missing,
              causing "Binder Error: no UNIQUE/PRIMARY KEY constraints" on
              every write_service INSERT ... ON CONFLICT attempt. Also added
              `source` column to mcp_threat_associations with UNIQUE scope.
  2026-05-28: Bumped wait_for_ws default 30s -> 120s. On cold container
              boot, write_service's wrapper does a 60s backoff after any
              early crash (malloc tcache, port binding contention). The
              old 30s window expired during that backoff, causing section
              18 of go.sh to abort with 'write_service not ready --
              skipping bootstrap'. 120s comfortably covers one full
              backoff cycle plus startup latency.
"""
import requests, time, logging, sys
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger()

WS = 'http://127.0.0.1:8772'

def wait_for_ws(max_wait=120):
    """Wait for write_service to be ready.

    Default bumped to 120s (was 30s) to ride out one full write_service
    wrapper backoff cycle (60s) plus startup latency. See HISTORY 2026-05-28.
    """
    for i in range(max_wait):
        try:
            r = requests.get(WS + '/health', timeout=3)
            if r.status_code == 200:
                log.info('write_service ready after %ds', i)
                return True
        except Exception:
            pass
        time.sleep(1)
    log.error('write_service not ready after %ds', max_wait)
    return False

def ex(sql, label):
    for attempt in range(3):
        try:
            r = requests.post(WS + '/execute',
                json={'sql': sql.strip(), 'wait': True}, timeout=30)
            if r.status_code == 200:
                log.info('[OK] %s', label)
                return True
            log.warning('[FAIL] %s: HTTP %s %s', label, r.status_code, r.text[:60])
            return False
        except requests.Timeout:
            log.warning('[RETRY %d] %s: timeout', attempt+1, label)
            time.sleep(2)
        except Exception as e:
            log.error('[ERR] %s: %s', label, e)
            return False
    return False

if not wait_for_ws():
    log.error('Aborting -- write_service unavailable')
    sys.exit(1)

# Sequences first
for seq in ['seq_signal_id','seq_threat_id','seq_decision_id',
            'seq_policy_id','seq_defhist_id','seq_id']:
    ex(f'CREATE SEQUENCE IF NOT EXISTS {seq} START 1', f'seq {seq}')

TABLES = [
    # CORE SENTINEL TABLES (normally created by schema.py)
    ("""CREATE TABLE IF NOT EXISTS mcp_server_registry (
        server_id        VARCHAR PRIMARY KEY,
        name             VARCHAR,
        registry_source  VARCHAR,
        url              VARCHAR,
        description      TEXT,
        trust_score      FLOAT,
        verdict          VARCHAR,
        verdict_reasoning TEXT,
        confidence       FLOAT,
        last_assessed    TIMESTAMPTZ,
        first_seen       TIMESTAMPTZ,
        last_seen        TIMESTAMPTZ,
        last_scanned     TIMESTAMPTZ,
        scan_count       INTEGER DEFAULT 0,
        risk_tier        VARCHAR,
        metadata         TEXT
    )""", 'mcp_server_registry'),

    # 2026-04-17: Added PRIMARY KEY(id) and UNIQUE(server_id, signal_name).
    # Without these, write_service INSERT ON CONFLICT fails with Binder Error.
    ("""CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        id           BIGINT PRIMARY KEY DEFAULT nextval('seq_signal_id'),
        server_id    VARCHAR NOT NULL,
        signal_name  VARCHAR NOT NULL,
        score        FLOAT,
        evidence     TEXT,
        scored_at    TIMESTAMPTZ DEFAULT now(),
        UNIQUE (server_id, signal_name)
    )""", 'mcp_signal_scores'),

    # 2026-04-17: Added PRIMARY KEY(id), `source` column, UNIQUE(server_id,
    # threat_type, source). Callers (threat_intel_ingestor, rug_pull_monitor,
    # pi_scorer) MUST set `source` explicitly to enable deduplication.
    ("""CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id           BIGINT PRIMARY KEY DEFAULT nextval('seq_threat_id'),
        server_id    VARCHAR NOT NULL,
        threat_type  VARCHAR NOT NULL,
        severity     VARCHAR,
        evidence     TEXT,
        source       VARCHAR,
        reported_at  TIMESTAMPTZ DEFAULT now(),
        UNIQUE (server_id, threat_type, source)
    )""", 'mcp_threat_associations'),

    # Indexes for query performance on long-format signal table
    ("""CREATE INDEX IF NOT EXISTS idx_signal_scores_server
        ON mcp_signal_scores(server_id)""", 'idx_signal_scores_server'),
    ("""CREATE INDEX IF NOT EXISTS idx_signal_scores_name
        ON mcp_signal_scores(signal_name)""", 'idx_signal_scores_name'),
    ("""CREATE INDEX IF NOT EXISTS idx_threats_server
        ON mcp_threat_associations(server_id)""", 'idx_threats_server'),
    ("""CREATE INDEX IF NOT EXISTS idx_threats_severity
        ON mcp_threat_associations(severity)""", 'idx_threats_severity'),

    ("""CREATE TABLE IF NOT EXISTS mcp_submissions (
        submission_id    VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        server_id        VARCHAR,
        mcp_name         VARCHAR NOT NULL,
        url              VARCHAR,
        description      TEXT,
        requested_by     VARCHAR,
        business_purpose TEXT,
        environment      VARCHAR,
        submitted_at     TIMESTAMPTZ DEFAULT now(),
        status           VARCHAR DEFAULT 'pending'
    )""", 'mcp_submissions'),

    ("""CREATE TABLE IF NOT EXISTS mcp_decisions (
        id            BIGINT DEFAULT nextval('seq_decision_id'),
        submission_id VARCHAR,
        analyst_name  VARCHAR,
        decision      VARCHAR,
        conditions    TEXT,
        notes         TEXT,
        expiry_days   INTEGER DEFAULT 90,
        expires_at    TIMESTAMPTZ,
        decided_at    TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_decisions'),

    ("""CREATE TABLE IF NOT EXISTS mcp_policy_rules (
        id          BIGINT DEFAULT nextval('seq_policy_id'),
        rule_name   VARCHAR UNIQUE NOT NULL,
        rule_type   VARCHAR,
        pattern     VARCHAR,
        action      VARCHAR,
        description TEXT,
        created_at  TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_policy_rules'),

    ("""CREATE TABLE IF NOT EXISTS mcp_definition_history (
        id            BIGINT DEFAULT nextval('seq_defhist_id'),
        server_id     VARCHAR,
        snapshot_hash VARCHAR,
        captured_at   TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_definition_history'),

    # SUPPLEMENTARY TABLES
    ("""CREATE TABLE IF NOT EXISTS mcp_risk_register (
        server_id            VARCHAR PRIMARY KEY,
        name                 VARCHAR,
        risk_rank            FLOAT,
        risk_tier            VARCHAR,
        threat_count         INTEGER DEFAULT 0,
        staleness_days       INTEGER DEFAULT 0,
        environment_exposure VARCHAR DEFAULT 'unknown',
        computed_at          TIMESTAMPTZ DEFAULT now()
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

    ("""CREATE TABLE IF NOT EXISTS audit_log (
        event_id         VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        event_type       VARCHAR,
        actor            VARCHAR,
        target_server_id VARCHAR,
        action           VARCHAR,
        outcome          VARCHAR,
        details_json     TEXT,
        immutable        BOOLEAN DEFAULT TRUE,
        timestamp        TIMESTAMPTZ DEFAULT now()
    )""", 'audit_log'),

    ("""CREATE TABLE IF NOT EXISTS mcp_tool_hashes (
        server_id    VARCHAR PRIMARY KEY,
        tools_hash   VARCHAR,
        tools_raw    TEXT,
        last_checked TIMESTAMPTZ DEFAULT now(),
        change_count INTEGER DEFAULT 0
    )""", 'mcp_tool_hashes'),

    ("""CREATE TABLE IF NOT EXISTS mcp_fingerprints (
        server_id              VARCHAR PRIMARY KEY,
        tool_name_hash         VARCHAR,
        description_tokens     TEXT,
        permission_scope_hash  VARCHAR,
        domain_fingerprint     VARCHAR,
        version_string         VARCHAR,
        computed_at            TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_fingerprints'),

    ("""CREATE TABLE IF NOT EXISTS mcp_exemptions (
        exemption_id  VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        server_id     VARCHAR NOT NULL,
        reason        TEXT,
        granted_by    VARCHAR,
        conditions_json TEXT,
        expires_at    TIMESTAMPTZ,
        active        BOOLEAN DEFAULT TRUE,
        created_at    TIMESTAMPTZ DEFAULT now()
    )""", 'mcp_exemptions'),

    ("""CREATE TABLE IF NOT EXISTS auth_tokens (
        token_id      VARCHAR PRIMARY KEY,
        action        VARCHAR,
        mcp_name      VARCHAR,
        submission_id VARCHAR,
        requested_by  VARCHAR,
        admin_email   VARCHAR,
        expires_at    TIMESTAMPTZ,
        used          BOOLEAN DEFAULT FALSE,
        used_at       TIMESTAMPTZ,
        created_at    TIMESTAMPTZ DEFAULT now()
    )""", 'auth_tokens'),

    ("""CREATE TABLE IF NOT EXISTS perf_metrics (
        service     VARCHAR,
        latency_ms  FLOAT,
        recorded_at TIMESTAMPTZ DEFAULT now()
    )""", 'perf_metrics'),

    ("""CREATE TABLE IF NOT EXISTS shodan_results (
        server_id      VARCHAR,
        ip_address     VARCHAR,
        open_ports     TEXT,
        cves_found     TEXT,
        exposure_score FLOAT,
        scanned_at     TIMESTAMPTZ DEFAULT now()
    )""", 'shodan_results'),

    ("""CREATE TABLE IF NOT EXISTS github_velocity (
        server_id              VARCHAR,
        repo_url               VARCHAR,
        commit_velocity        FLOAT,
        contributor_churn      FLOAT,
        last_suspicious_commit VARCHAR,
        checked_at             TIMESTAMPTZ DEFAULT now()
    )""", 'github_velocity'),

    ("""CREATE TABLE IF NOT EXISTS npm_typosquat_alerts (
        id               BIGINT DEFAULT nextval('seq_id'),
        suspect_name     VARCHAR,
        target_name      VARCHAR,
        levenshtein_dist INTEGER,
        npm_downloads    INTEGER DEFAULT 0,
        published_at     TIMESTAMPTZ,
        flagged_at       TIMESTAMPTZ DEFAULT now()
    )""", 'npm_typosquat_alerts'),

    # build_provenance -- bake-off + API-picker seed corpus (added 2026-05-22)
    ("""CREATE TABLE IF NOT EXISTS build_provenance (
        build_id       VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
        directive_id   VARCHAR,
        directive_type VARCHAR,
        complexity     VARCHAR,
        engine         VARCHAR,
        model          VARCHAR,
        backend        VARCHAR,
        smoke_result   VARCHAR,
        rescue_count   INTEGER DEFAULT 0,
        success        BOOLEAN,
        output_path    VARCHAR,
        output_bytes   INTEGER,
        error          TEXT,
        built_at       TIMESTAMPTZ DEFAULT now()
    )""", 'build_provenance'),
]

created = sum(1 for sql, label in TABLES if ex(sql, label))
log.info('Bootstrap complete: %d/%d tables OK', created, len(TABLES))

# Verify the critical one
try:
    r = requests.post(WS + '/query',
        json={'sql': 'SELECT COUNT(*) as n FROM mcp_server_registry'},
        timeout=10)
    if r.status_code == 200:
        n = r.json().get('rows', [{}])[0].get('n', '?')
        log.info('mcp_server_registry verified: %s rows', n)
    else:
        log.error('mcp_server_registry verification failed: HTTP %s', r.status_code)
except Exception as e:
    log.error('Verification error: %s', e)

if created < len(TABLES):
    log.warning('%d table(s) failed -- run again or check write_service', len(TABLES)-created)
    sys.exit(1)
else:
    log.info('All tables ready.')