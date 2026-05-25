#!/usr/bin/env python3
"""
schema.py -- ZO-SENTINEL DuckDB schema creation.
Creates all tables via write_service execute endpoint on port 8772.
Run once on startup or call create_all() from any service.
"""
import requests, logging

log = logging.getLogger(__name__)
EXECUTE_URL = "http://127.0.0.1:8772/execute"

TABLES = [
    """
    CREATE TABLE IF NOT EXISTS mcp_server_registry (
        id             BIGINT PRIMARY KEY,
        server_id      VARCHAR UNIQUE NOT NULL,
        name           VARCHAR,
        registry_source VARCHAR,
        url            VARCHAR,
        description    TEXT,
        trust_score    REAL,
        verdict        VARCHAR,
        verdict_reasoning TEXT,
        confidence     REAL,
        last_assessed  TIMESTAMPTZ,
        first_seen     TIMESTAMPTZ DEFAULT now(),
        last_seen      TIMESTAMPTZ DEFAULT now(),
        scan_count     INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        score       REAL,
        evidence    TEXT,
        scored_at   TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_definition_history (
        id               BIGINT PRIMARY KEY,
        server_id        VARCHAR NOT NULL,
        snapshot_hash    VARCHAR,
        snapshot_content TEXT,
        captured_at      TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        threat_type VARCHAR,
        evidence    TEXT,
        severity    VARCHAR,
        reported_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_submissions (
        id             BIGINT PRIMARY KEY,
        submission_id  VARCHAR UNIQUE NOT NULL,
        mcp_identifier VARCHAR NOT NULL,
        requester_name VARCHAR,
        requester_team VARCHAR,
        business_purpose TEXT,
        environment    VARCHAR,
        submitted_at   TIMESTAMPTZ DEFAULT now(),
        status         VARCHAR DEFAULT 'pending'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_decisions (
        id           BIGINT PRIMARY KEY,
        submission_id VARCHAR NOT NULL,
        analyst_name VARCHAR,
        decision     VARCHAR,
        conditions   TEXT,
        notes        TEXT,
        expiry_days  INTEGER DEFAULT 90,
        expires_at   TIMESTAMPTZ,
        decided_at   TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_policy_rules (
        id          BIGINT PRIMARY KEY,
        rule_name   VARCHAR UNIQUE NOT NULL,
        rule_type   VARCHAR,
        pattern     VARCHAR,
        action      VARCHAR,
        description TEXT,
        created_at  TIMESTAMPTZ DEFAULT now()
    )
    """
]


def _execute(sql: str) -> bool:
    """Send a single SQL statement to write_service execute endpoint."""
    try:
        r = requests.post(EXECUTE_URL,
            json={"sql": sql.strip(), "wait": True},
            timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"schema execute error: {e}")
        return False


def create_all() -> int:
    """Create all ZO-SENTINEL tables. Returns count of successful statements."""
    created = 0
    for sql in TABLES:
        if _execute(sql):
            created += 1
        else:
            log.warning(f"Table creation failed for: {sql.strip()[:60]}")
    log.info(f"Schema: {created}/{len(TABLES)} tables created/verified")
    return created


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_all()