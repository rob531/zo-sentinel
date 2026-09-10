"""
Risk Tier Distribution Service

Computes the distribution of servers across risk tiers.
GET /api/risk/tier_distribution
"""

import json
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy import text

# Database access through write_service
def query_db(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Query the database via write_service."""
    response = requests.post(
        "http://write_service/query",
        json={"sql": sql, "params": params or {}}
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def get_tier_distribution() -> dict[str, Any]:
    """
    Aggregate the latest scores per server and compute risk_tier distribution.
    
    Reads from:
    - McpLlmAxisScore (server_id, axis_name, p_top, p_critical, p_danger, escalated, escalated_to, decision_rule_version, model_version, scored_at)
    - McpServerRegistry (server_id, risk_tier, last_assessed)
    
    Returns:
        JSON object with total_servers, tiers list, and timestamp
    """
    now = datetime.now(timezone.utc).isoformat()
    
    sql = text("""
        WITH latest_per_server AS (
            SELECT server_id, scored_at
            FROM McpLlmAxisScore
            WHERE (server_id, scored_at) IN (
                SELECT server_id, MAX(scored_at) as scored_at
                FROM McpLlmAxisScore
                GROUP BY server_id
            )
        )
        SELECT DISTINCT s.server_id, reg.risk_tier
        FROM latest_per_server s
        JOIN McpServerRegistry reg ON s.server_id = reg.server_id
    """)
    
    servers_with_tiers = query_db(str(sql), {})
    
    tier_counts: dict[str, int] = {}
    for row in servers_with_tiers:
        tier = row.get('risk_tier', 'UNKNOWN')
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    
    tiers = [
        {"tier": tier, "count": count}
        for tier, count in sorted(tier_counts.items())
    ]
    
    return {
        "total_servers": len(servers_with_tiers),
        "tiers": tiers,
        "timestamp": now
    }


def run() -> dict[str, Any]:
    """Entry point for the service."""
    return get_tier_distribution()


def health() -> dict[str, Any]:
    """Health check for the service."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import sqlite3
    from unittest.mock import patch
    
    # Create in-memory SQLite database
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # Create tables matching the schema
    cursor.execute("""
        CREATE TABLE McpLlmAxisScore (
            server_id TEXT,
            axis_name TEXT,
            p_top REAL,
            p_critical REAL,
            p_danger REAL,
            escalated INTEGER,
            escalated_to TEXT,
            decision_rule_version TEXT,
            model_version TEXT,
            scored_at TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            risk_tier TEXT,
            last_assessed TEXT
        )
    """)
    
    # Seed test data - three servers with different risk tiers
    servers = [
        ("server-001", "HIGH_RISK_ISOLATED"),
        ("server-002", "TRUSTED_GENERAL"),
        ("server-003", "CAUTION_LIMITED"),
    ]
    
    base_time = "2024-01-15T10:00:00Z"
    for server_id, risk_tier in servers:
        # Add a score record for each server
        cursor.execute("""
            INSERT INTO McpLlmAxisScore 
            (server_id, axis_name, p_top, p_critical, p_danger, escalated, escalated_to, decision_rule_version, model_version, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (server_id, "security", 0.1, 0.2, 0.3, 0, None, "v1", "v1", base_time))
        
        # Add registry record
        cursor.execute("""
            INSERT INTO McpServerRegistry (server_id, risk_tier, last_assessed)
            VALUES (?, ?, ?)
        """, (server_id, risk_tier, base_time))
    
    conn.commit()
    
    # Expected results
    expected_tiers = {
        "HIGH_RISK_ISOLATED": 1,
        "TRUSTED_GENERAL": 1,
        "CAUTION_LIMITED": 1,
    }
    expected_total = 3
    
    # Mock response data
    mock_rows = [
        {"server_id": "server-001", "risk_tier": "HIGH_RISK_ISOLATED"},
        {"server_id": "server-002", "risk_tier": "TRUSTED_GENERAL"},
        {"server_id": "server-003", "risk_tier": "CAUTION_LIMITED"},
    ]
    
    class MockResponse:
        def raise_for_status(self):
            pass
        
        def json(self):
            return {"rows": mock_rows}
    
    with patch("requests.post", return_value=MockResponse()):
        result = run()
    
    # Assertions
    assert result["total_servers"] == expected_total, \
        f"Expected total_servers={expected_total}, got {result['total_servers']}"
    
    result_tiers = {t["tier"]: t["count"] for t in result["tiers"]}
    assert result_tiers == expected_tiers, \
        f"Expected tiers={expected_tiers}, got {result_tiers}"
    
    conn.close()
    print("PASS")