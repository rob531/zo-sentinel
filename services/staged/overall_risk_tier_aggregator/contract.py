"""
Overall Risk Tier Aggregator Service Contract

This service reads axis scores from mcp_llm_axis_scores, computes composite
risk tiers for each server, and writes results to mcp_server_registry.
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# Data layer imports
from app.db import get_session
from app.models import McpLlmAxisScore as AxisScoreModel
from app.models import McpServerRegistry as ServerRegistryModel


# Risk tier thresholds
RISK_TIER_THRESHOLDS = [
    (75, "TRUSTED_GENERAL"),
    (60, "TRUSTED_RESEARCH"),
    (45, "ENTERPRISE_CONTROLLED"),
    (30, "CAUTION_LIMITED"),
    (15, "HIGH_RISK_ISOLATED"),
    (0, "KNOWN_THREAT"),
]


def compute_risk_tier(avg_p_top: float) -> str:
    """Map average p_top to risk tier based on thresholds."""
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if avg_p_top > threshold:
            return tier
    return "KNOWN_THREAT"


def get_avg_axis_scores(session: Session, cutoff: datetime) -> List[Dict[str, Any]]:
    """
    Fetch average p_top scores grouped by server_id for scores within the time window.
    
    Args:
        session: Database session
        cutoff: Timestamp cutoff (typically now - 24 hours)
    
    Returns:
        List of dicts with server_id and avg_p_top
    """
    query = text("""
        SELECT server_id, AVG(p_top) as avg_p_top
        FROM mcp_llm_axis_scores
        WHERE scored_at >= :cutoff
        GROUP BY server_id
    """)
    result = session.execute(query, {"cutoff": cutoff})
    return [{"server_id": row[0], "avg_p_top": row[1]} for row in result.fetchall()]


def update_server_risk_tier(
    session: Session,
    server_id: str,
    risk_tier: str,
    last_assessed: datetime
) -> None:
    """Update risk_tier and last_assessed for a server."""
    query = text("""
        UPDATE mcp_server_registry
        SET risk_tier = :risk_tier,
            last_assessed = :last_assessed
        WHERE server_id = :server_id
    """)
    session.execute(query, {
        "server_id": server_id,
        "risk_tier": risk_tier,
        "last_assessed": last_assessed
    })


def compute_and_update_all_risk_tiers(session: Session) -> Dict[str, str]:
    """
    Main logic: compute risk tiers for all servers with recent axis scores
    and update their registry entries.
    
    Returns:
        Dict mapping server_id to computed risk_tier
    """
    cutoff = datetime.utcnow() - timedelta(hours=24)
    
    # Get average axis scores per server
    server_scores = get_avg_axis_scores(session, cutoff)
    
    # Compute and update risk tiers
    results = {}
    now = datetime.utcnow()
    
    for item in server_scores:
        server_id = item["server_id"]
        avg_p_top = item["avg_p_top"]
        
        risk_tier = compute_risk_tier(avg_p_top)
        update_server_risk_tier(session, server_id, risk_tier, now)
        results[server_id] = risk_tier
    
    session.commit()
    return results


def get_url_collision_report(session: Session) -> List[Dict[str, Any]]:
    """Get servers with duplicate URL patterns."""
    query = text("""
        SELECT server_id, url, COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY url
        HAVING COUNT(*) > 1
    """)
    result = session.execute(query)
    return [{"server_id": row[0], "url": row[1], "count": row[2]} for row in result.fetchall()]


def _fetch_latest_axis_scores(session: Session, server_id: str) -> List[Dict[str, Any]]:
    """Fetch the most recent axis scores for a server."""
    query = text("""
        SELECT * FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
        ORDER BY scored_at DESC
        LIMIT 1
    """)
    result = session.execute(query, {"server_id": server_id})
    row = result.fetchone()
    if row is None:
        return []
    return [{"server_id": row[0], "scored_at": row[1], "p_top": row[2]}]


def _latest_model_version(session: Session, server_id: str) -> Optional[str]:
    """Get the latest model version for a server's axis scores."""
    query = text("""
        SELECT model_version FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
        ORDER BY scored_at DESC
        LIMIT 1
    """)
    result = session.execute(query, {"server_id": server_id})
    row = result.fetchone()
    return row[0] if row else None


def get_advisories_by_family(session: Session, family: str) -> List[Dict[str, Any]]:
    """Get advisories filtered by CVE family."""
    query = text("""
        SELECT * FROM cve_advisories
        WHERE family = :family
    """)
    result = session.execute(query, {"family": family})
    return [dict(row._mapping) for row in result.fetchall()]


def get_evidence_by_hash(session: Session, file_hash: str) -> List[Dict[str, Any]]:
    """Get evidence records by file hash."""
    query = text("""
        SELECT * FROM evidence
        WHERE file_hash = :file_hash
    """)
    result = session.execute(query, {"file_hash": file_hash})
    return [dict(row._mapping) for row in result.fetchall()]


def get_timeline(session: Session, server_id: str) -> List[Dict[str, Any]]:
    """Get timeline events for a server."""
    query = text("""
        SELECT * FROM timeline_events
        WHERE server_id = :server_id
        ORDER BY event_time DESC
    """)
    result = session.execute(query, {"server_id": server_id})
    return [dict(row._mapping) for row in result.fetchall()]


def get_overview_summary(session: Session) -> Dict[str, Any]:
    """Get overview summary statistics."""
    query = text("""
        SELECT 
            COUNT(*) as total_servers,
            COUNT(DISTINCT risk_tier) as unique_tiers
        FROM mcp_server_registry
    """)
    result = session.execute(query)
    row = result.fetchone()
    return {
        "total_servers": row[0],
        "unique_tiers": row[1]
    }


def get_queue_test(session: Session) -> Dict[str, Any]:
    """Get queue test status."""
    return {"status": "ok", "queue_name": "risk_tier_transitions"}


def get_risk_transitions(session: Session, server_id: str) -> List[Dict[str, Any]]:
    """Get risk tier transitions for a server."""
    query = text("""
        SELECT * FROM risk_tier_transitions
        WHERE server_id = :server_id
        ORDER BY transitioned_at DESC
    """)
    result = session.execute(query, {"server_id": server_id})
    return [dict(row._mapping) for row in result.fetchall()]


def get_server_scorecard_badge(session: Session, server_id: str) -> Dict[str, Any]:
    """Get scorecard badge for a server."""
    query = text("""
        SELECT server_id, risk_tier, last_assessed
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """)
    result = session.execute(query, {"server_id": server_id})
    row = result.fetchone()
    if row is None:
        return {"error": "Server not found"}
    return {
        "server_id": row[0],
        "risk_tier": row[1],
        "last_assessed": row[2]
    }


def list_directory(session: Session, limit: int = 100) -> List[Dict[str, Any]]:
    """List servers in the directory."""
    query = text("""
        SELECT server_id, name, url, risk_tier
        FROM mcp_server_registry
        LIMIT :limit
    """)
    result = session.execute(query, {"limit": limit})
    return [dict(row._mapping) for row in result.fetchall()]


def get_test_session():
    """Get test session for self-testing."""
    from app.db import get_session as original_get_session
    return original_get_session


# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == "__main__":
    import threading
    import time
    from unittest.mock import patch, MagicMock
    from sqlalchemy.pool import StaticPool
    
    print("Running overall_risk_tier_aggregator self-test...")
    
    # Create in-memory SQLite database for testing
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables
    with test_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                server_id TEXT,
                scored_at TIMESTAMP,
                p_top REAL,
                model_version TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                risk_tier TEXT,
                last_assessed TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS service_health (
                service_name TEXT PRIMARY KEY,
                last_heartbeat TIMESTAMP,
                status TEXT
            )
        """))
        conn.commit()
    
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Seed test data: 3 servers with axis rows across 3 days
    base_time = datetime.utcnow()
    
    with test_engine.connect() as conn:
        # Server 1: TRUSTED_GENERAL (avg p_top > 75)
        # Day 1: 80, Day 2: 82, Day 3: 78 -> avg = 80
        for i, score in enumerate([80.0, 82.0, 78.0]):
            day_offset = i * 24
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores (server_id, scored_at, p_top, model_version)
                VALUES (:server_id, :scored_at, :p_top, :model_version)
            """), {
                "server_id": "server_trusted",
                "scored_at": base_time - timedelta(hours=day_offset),
                "p_top": score,
                "model_version": "test-v1"
            })
        
        # Server 2: ENTERPRISE_CONTROLLED (avg p_top > 45, <= 60)
        # Day 1: 50, Day 2: 48, Day 3: 52 -> avg = 50
        for i, score in enumerate([50.0, 48.0, 52.0]):
            day_offset = i * 24
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores (server_id, scored_at, p_top, model_version)
                VALUES (:server_id, :scored_at, :p_top, :model_version)
            """), {
                "server_id": "server_enterprise",
                "scored_at": base_time - timedelta(hours=day_offset),
                "p_top": score,
                "model_version": "test-v1"
            })
        
        # Server 3: HIGH_RISK_ISOLATED (avg p_top > 15, <= 30)
        # Day 1: 20, Day 2: 18, Day 3: 22 -> avg = 20
        for i, score in enumerate([20.0, 18.0, 22.0]):
            day_offset = i * 24
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores (server_id, scored_at, p_top, model_version)
                VALUES (:server_id, :scored_at, :p_top, :model_version)
            """), {
                "server_id": "server_risky",
                "scored_at": base_time - timedelta(hours=day_offset),
                "p_top": score,
                "model_version": "test-v1"
            })
        
        # Register servers in registry
        for server_id in ["server_trusted", "server_enterprise", "server_risky"]:
            conn.execute(text("""
                INSERT INTO mcp_server_registry (server_id, name, url, risk_tier, last_assessed)
                VALUES (:server_id, :name, :url, :risk_tier, :last_assessed)
            """), {
                "server_id": server_id,
                "name": f"Test {server_id}",
                "url": f"https://{server_id}.test",
                "risk_tier": None,
                "last_assessed": None
            })
        
        conn.commit()
    
    # Mock app.db.get_session
    with patch("app.db.get_session", override_get_session):
        # Mock requests for heartbeat (no actual network call)
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            
            # Run the main logic
            db = TestSessionLocal()
            try:
                results = compute_and_update_all_risk_tiers(db)
                
                # Verify results
                assert "server_trusted" in results, "Missing server_trusted in results"
                assert "server_enterprise" in results, "Missing server_enterprise in results"
                assert "server_risky" in results, "Missing server_risky in results"
                
                assert results["server_trusted"] == "TRUSTED_GENERAL", \
                    f"Expected TRUSTED_GENERAL for server_trusted, got {results['server_trusted']}"
                assert results["server_enterprise"] == "ENTERPRISE_CONTROLLED", \
                    f"Expected ENTERPRISE_CONTROLLED for server_enterprise, got {results['server_enterprise']}"
                assert results["server_risky"] == "HIGH_RISK_ISOLATED", \
                    f"Expected HIGH_RISK_ISOLATED for server_risky, got {results['server_risky']}"
                
                # Verify registry was updated
                with test_engine.connect() as conn:
                    for server_id, expected_tier in [
                        ("server_trusted", "TRUSTED_GENERAL"),
                        ("server_enterprise", "ENTERPRISE_CONTROLLED"),
                        ("server_risky", "HIGH_RISK_ISOLATED"),
                    ]:
                        result = conn.execute(text("""
                            SELECT risk_tier, last_assessed
                            FROM mcp_server_registry
                            WHERE server_id = :server_id
                        """), {"server_id": server_id}).fetchone()
                        
                        assert result[0] == expected_tier, \
                            f"Registry not updated: {server_id} has {result[0]}, expected {expected_tier}"
                        assert result[1] is not None, \
                            f"last_assessed not set for {server_id}"
                
                # Test helper functions
                assert get_url_collision_report(db) == [], "get_url_collision_report failed"
                
                axis_scores = _fetch_latest_axis_scores(db, "server_trusted")
                assert len(axis_scores) > 0, "_fetch_latest_axis_scores failed"
                
                version = _latest_model_version(db, "server_trusted")
                assert version == "test-v1", "_latest_model_version failed"
                
                summary = get_overview_summary(db)
                assert summary["total_servers"] == 3, "get_overview_summary failed"
                
                badge = get_server_scorecard_badge(db, "server_trusted")
                assert badge["risk_tier"] == "TRUSTED_GENERAL", "get_server_scorecard_badge failed"
                
                directory = list_directory(db)
                assert len(directory) == 3, "list_directory failed"
                
                print("All assertions passed!")
                
            finally:
                db.close()
    
    print("PASS")
    sys.exit(0)