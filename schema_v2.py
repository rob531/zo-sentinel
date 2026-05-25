#!/usr/bin/env python3
"""
schema_v2.py -- ZO-SENTINEL extended schema for approval workflow.
Adds mcp_submissions, mcp_decisions, mcp_policy_rules tables.
Call create_v2() after create_all() from schema.py.
"""
import requests, logging

log = logging.getLogger(__name__)
EXECUTE_URL = "http://127.0.0.1:8772/execute"

V2_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS mcp_submissions (
        id               BIGINT PRIMARY KEY,
        submission_id    VARCHAR UNIQUE NOT NULL,
        mcp_identifier   VARCHAR NOT NULL,
        requester_name   VARCHAR,
        requester_team   VARCHAR,
        business_purpose TEXT,
        environment      VARCHAR,
        submitted_at     TIMESTAMPTZ DEFAULT now(),
        status           VARCHAR DEFAULT 'pending'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_decisions (
        id            BIGINT PRIMARY KEY,
        submission_id VARCHAR NOT NULL,
        analyst_name  VARCHAR,
        decision      VARCHAR,
        conditions    TEXT,
        notes         TEXT,
        expiry_days   INTEGER DEFAULT 90,
        expires_at    TIMESTAMPTZ,
        decided_at    TIMESTAMPTZ DEFAULT now()
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

DEFAULT_POLICIES = [
    {
        "rule_name":   "block_shell_on_production",
        "rule_type":   "PERMISSION_BLOCK",
        "pattern":     "shell|execute|subprocess",
        "action":      "BLOCK",
        "description": "Never allow MCPs requesting shell/execute in production environment"
    },
    {
        "rule_name":   "escalate_low_trust",
        "rule_type":   "SIGNAL_THRESHOLD",
        "pattern":     "trust_score<45",
        "action":      "ESCALATE",
        "description": "Escalate any MCP with trust_score below 45 to senior review"
    },
    {
        "rule_name":   "block_known_threat",
        "rule_type":   "VERDICT_BLOCK",
        "pattern":     "KNOWN_THREAT",
        "action":      "BLOCK",
        "description": "Automatically block any MCP with KNOWN_THREAT verdict"
    }
]


def _execute(sql: str) -> bool:
    try:
        r = requests.post(EXECUTE_URL,
            json={"sql": sql.strip(), "wait": True},
            timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"execute error: {e}")
        return False


def _ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post("http://127.0.0.1:8772/write",
            json={"table": table, "rows": row, "wait": True},
            timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"write error: {e}")
        return False


def create_v2() -> int:
    """Create v2 tables. Returns count of successful statements."""
    created = 0
    for sql in V2_TABLES:
        if _execute(sql):
            created += 1
    log.info(f"Schema v2: {created}/{len(V2_TABLES)} tables created/verified")
    return created


def seed_default_policies():
    """Write sensible default policy rules to mcp_policy_rules."""
    seeded = 0
    for i, policy in enumerate(DEFAULT_POLICIES):
        policy["id"] = 1000 + i  # stable IDs for defaults
        if _ws_write("mcp_policy_rules", policy):
            seeded += 1
            log.info(f"  Policy seeded: {policy['rule_name']}")
    log.info(f"Seeded {seeded}/{len(DEFAULT_POLICIES)} default policies")
    return seeded


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_v2()
    seed_default_policies()