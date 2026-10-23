"""
contract.py - scoring_consumer_maintainer_trust service contract

Service: scoring-consumer maintainer trust
Purpose: Reads mcp_llm_axis_scores for maintainer_trust axis, combines with registry
         metadata, applies trust decay rules, and writes refined trust scores.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field


class MaintainerTrustInput(BaseModel):
    """Input model for maintainer trust scoring calculation."""
    server_id: str
    p_top: float = Field(description="Probability score for top tier")
    p_critical: float = Field(description="Probability score for critical tier")
    registry_source: str
    scan_count: int
    last_scanned: datetime


class MaintainerTrustResult(BaseModel):
    """Result model for maintainer trust scoring."""
    server_id: str
    maintainer_trust_score: float = Field(ge=0, le=100, description="Trust score 0-100")
    risk_tier: str = Field(description="Risk tier: HIGH, MEDIUM, LOW, or CRITICAL")
    decay_factors: list[str] = Field(default_factory=list, description="List of active decay factors")


def _create_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database for testing."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            p_top REAL,
            p_critical REAL,
            created_at TEXT
        )
    """)
    
    conn.execute("""
        CREATE TABLE mcp_server_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            registry_source TEXT,
            scan_count INTEGER,
            last_scanned TEXT,
            maintainer_trust_score REAL,
            risk_tier TEXT,
            decay_factors TEXT
        )
    """)
    
    conn.execute("""
        CREATE TABLE threat_intel_refs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            pulse_id TEXT,
            created_at TEXT
        )
    """)
    
    conn.commit()
    return conn


def _seed_test_data(conn: sqlite3.Connection) -> None:
    """Seed test database with mixed maintainer_trust scenarios."""
    now = datetime(2024, 2, 19)
    
    thirty_two_days_ago = (now - timedelta(days=32)).strftime("%Y-%m-%dT%H:%M:%SZ")
    thirty_days_ago = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fifteen_days_ago = (now - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    two_days_ago = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    conn.executemany(
        "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("server1", "maintainer_trust", 0.6, 0.2, fifteen_days_ago),
            ("server2", "maintainer_trust", 0.5, 0.3, thirty_two_days_ago),
            ("server3", "maintainer_trust", 0.8, 0.1, two_days_ago),
            ("server4", "maintainer_trust", 0.9, 0.05, two_days_ago),
        ]
    )
    
    conn.executemany(
        "INSERT INTO mcp_server_registry (server_id, registry_source, scan_count, last_scanned) VALUES (?, ?, ?, ?)",
        [
            ("server1", "npm", 2, thirty_days_ago),       # stale (>30d) + low count
            ("server2", "pypi", 1, thirty_two_days_ago),   # stale + low count
            ("server3", "github", 1, two_days_ago),        # fresh but low count
            ("server4", "npm", 10, two_days_ago),          # fresh + good count
        ]
    )
    
    conn.executemany(
        "INSERT INTO threat_intel_refs (server_id, pulse_id, created_at) VALUES (?, ?, ?)",
        [
            ("server1", "pulse_abc123", two_days_ago),
            ("server2", "pulse_def456", thirty_two_days_ago),
        ]
    )
    
    conn.commit()


def _run_scoring(conn: sqlite3.Connection) -> None:
    """Execute maintainer trust scoring logic on test data."""
    cursor = conn.execute(
        "SELECT server_id, axis_name FROM mcp_llm_axis_scores WHERE axis_name = ?",
        ("maintainer_trust",)
    )
    servers = [row["server_id"] for row in cursor.fetchall()]
    
    for server_id in servers:
        axis_cursor = conn.execute(
            "SELECT p_top, p_critical FROM mcp_llm_axis_scores WHERE server_id = ? AND axis_name = ?",
            (server_id, "maintainer_trust")
        )
        axis_row = axis_cursor.fetchone()
        if not axis_row:
            continue
        p_top = axis_row["p_top"]
        p_critical = axis_row["p_critical"]
        
        reg_cursor = conn.execute(
            "SELECT registry_source, scan_count, last_scanned FROM mcp_server_registry WHERE server_id = ?",
            (server_id,)
        )
        reg_row = reg_cursor.fetchone()
        if not reg_row:
            continue
        registry_source = reg_row["registry_source"]
        scan_count = reg_row["scan_count"]
        last_scanned_str = reg_row["last_scanned"]
        last_scanned = datetime.strptime(last_scanned_str, "%Y-%m-%dT%H:%M:%SZ")
        
        threat_cursor = conn.execute(
            "SELECT pulse_id FROM threat_intel_refs WHERE server_id = ?",
            (server_id,)
        )
        has_pulse = threat_cursor.fetchone() is not None
        
        decay_factors = []
        if scan_count < 3:
            decay_factors.append("low_scan_count")
        if (datetime(2024, 2, 19) - last_scanned).days > 30:
            decay_factors.append("stale_last_scanned")
        
        base_score = p_top * 100
        decay_multiplier = 0.7 if len(decay_factors) == 2 else (0.85 if len(decay_factors) == 1 else 1.0)
        maintainer_trust_score = round(base_score * decay_multiplier, 2)
        
        if maintainer_trust_score >= 75:
            risk_tier = "HIGH"
        elif maintainer_trust_score >= 50:
            risk_tier = "MEDIUM"
        elif maintainer_trust_score >= 25:
            risk_tier = "LOW"
        else:
            risk_tier = "CRITICAL"
        
        conn.execute(
            """UPDATE mcp_server_registry 
               SET maintainer_trust_score = ?, risk_tier = ?, decay_factors = ?
               WHERE server_id = ?""",
            (maintainer_trust_score, risk_tier, str(decay_factors), server_id)
        )
        conn.commit()


if __name__ == "__main__":
    conn = _create_test_db()
    _seed_test_data(conn)
    _run_scoring(conn)
    
    cursor = conn.execute(
        "SELECT server_id, maintainer_trust_score, risk_tier, decay_factors FROM mcp_server_registry ORDER BY server_id"
    )
    rows = cursor.fetchall()
    
    results = [(row["server_id"], row["maintainer_trust_score"], row["risk_tier"], row["decay_factors"]) for row in rows]
    
    print(f"Results: {results}")
    
    assert len(results) == 4, f"Expected 4 results, got {len(results)}"
    
    score_map = {}
    for server_id, score, tier, decay in results:
        assert isinstance(score, float), f"Score must be float for {server_id}"
        assert 0 <= score <= 100, f"Score {score} out of range for {server_id}"
        assert tier is not None, f"Risk tier is null for {server_id}"
        score_map[server_id] = score
        print(f"  {server_id}: score={score}, tier={tier}, decay={decay}")
    
    server1_decay = results[0][3]
    server2_decay = results[1][3]
    server3_decay = results[2][3]
    server4_decay = results[3][3]
    
    assert "low_scan_count" in server1_decay or "stale_last_scanned" in server1_decay
    assert "stale_last_scanned" in server2_decay
    assert "low_scan_count" in server3_decay
    assert server4_decay == "[]"
    
    assert score_map["server4"] > score_map["server2"], "server4 should score higher than server2 (fresh vs stale)"
    assert score_map["server4"] > score_map["server3"], "server4 should score higher than server3 (good count vs low count)"
    assert score_map["server1"] > score_map["server2"], "server1 should score higher than server2 (less decay)"
    
    conn.close()
    print("PASS")
    sys.exit(0)