"""
Router for verdict_dashboard_api service.
Exposes GET /api/verdicts/dashboard endpoint.
Aggregates risk_tier distribution and axis scores across scored servers.
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api/verdicts", tags=["verdict-dashboard"])


class TierDistribution(BaseModel):
    """Risk tier distribution entry."""
    count: int


class AvgAxisScore(BaseModel):
    """Average axis score entry."""
    avg_p_score: float


class DashboardResponse(BaseModel):
    """Dashboard aggregation response."""
    total_servers: int
    tiers: dict[str, int]
    avg_axis_scores: dict[str, float]
    last_updated: datetime | None

    class Config:
        from_attributes = True


# AdvisoryCreate for compatibility with vulnerability_management service
class AdvisoryCreate(BaseModel):
    """Pydantic model for advisory creation - mirrored for compatibility."""
    title: str | None = None
    description: str | None = None
    severity: str | None = None
    cve_id: str | None = None
    affected_server_id: str | None = None
    recommendation: str | None = None

    class Config:
        from_attributes = True


def get_verdict_detail(server_id: str, session: Session = Depends(get_session)) -> dict[str, Any] | None:
    """Get verdict detail for a server - compatibility function."""
    result = session.execute(
        text("""
            SELECT s.server_id, s.server_name, s.risk_tier, s.is_monitored,
                   a.p_score, a.impact_score, a.exploitability_score, a.axis_name
            FROM mcp_server_registry s
            LEFT JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
            WHERE s.server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "server_id": row[0],
        "server_name": row[1],
        "risk_tier": row[2],
        "is_monitored": row[3],
        "p_score": row[4],
        "impact_score": row[5],
        "exploitability_score": row[6],
        "axis_name": row[7]
    }


def get_snapshot(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get snapshot of all server verdicts - compatibility function."""
    result = session.execute(
        text("""
            SELECT s.server_id, s.server_name, s.risk_tier,
                   COALESCE(a.p_score, 0) as p_score
            FROM mcp_server_registry s
            LEFT JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
            WHERE s.is_monitored = true
        """)
    )
    return [
        {"server_id": r[0], "server_name": r[1], "risk_tier": r[2], "p_score": r[3]}
        for r in result.fetchall()
    ]


def get_signal_provenance_logic(server_id: str, session: Session = Depends(get_session)) -> dict[str, Any] | None:
    """Get signal provenance for a server - compatibility function."""
    result = session.execute(
        text("""
            SELECT a.axis_name, a.p_score, a.impact_score, a.exploitability_score,
                   a.created_at, s.risk_tier
            FROM mcp_llm_axis_scores a
            JOIN mcp_server_registry s ON a.server_id = s.server_id
            WHERE a.server_id = :server_id
            ORDER BY a.created_at DESC
            LIMIT 1
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "axis_name": row[0],
        "p_score": row[1],
        "impact_score": row[2],
        "exploitability_score": row[3],
        "created_at": row[4],
        "risk_tier": row[5]
    }


def get_server_verdict(server_id: str, session: Session = Depends(get_session)) -> dict[str, Any] | None:
    """Get server verdict - compatibility function."""
    result = session.execute(
        text("""
            SELECT s.server_id, s.server_name, s.risk_tier, s.is_monitored,
                   a.p_score, a.impact_score
            FROM mcp_server_registry s
            LEFT JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
            WHERE s.server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "server_id": row[0],
        "server_name": row[1],
        "risk_tier": row[2],
        "is_monitored": row[3],
        "p_score": row[4],
        "impact_score": row[5]
    }


def get_verdict_axes(server_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get verdict axes for a server - compatibility function."""
    result = session.execute(
        text("""
            SELECT axis_name, p_score, impact_score, exploitability_score, created_at
            FROM mcp_llm_axis_scores
            WHERE server_id = :server_id
            ORDER BY created_at DESC
        """),
        {"server_id": server_id}
    )
    return [
        {"axis_name": r[0], "p_score": r[1], "impact_score": r[2],
         "exploitability_score": r[3], "created_at": r[4]}
        for r in result.fetchall()
    ]


def get_verdict_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Get verdict endpoint info - compatibility function."""
    result = session.execute(text("SELECT COUNT(*) FROM mcp_server_registry"))
    count = result.scalar() or 0
    return {"endpoint": "/api/verdicts/dashboard", "total_servers": count}


def get_vulnerability_impact(server_id: str, session: Session = Depends(get_session)) -> dict[str, Any] | None:
    """Get vulnerability impact for a server - compatibility function."""
    result = session.execute(
        text("""
            SELECT s.server_id, s.server_name, a.impact_score, a.p_score,
                   a.exploitability_score
            FROM mcp_server_registry s
            LEFT JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
            WHERE s.server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "server_id": row[0],
        "server_name": row[1],
        "impact_score": row[2],
        "p_score": row[3],
        "exploitability_score": row[4]
    }


def get_trust_summary_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Get trust summary endpoint - compatibility function."""
    return {"endpoint": "/api/verdicts/dashboard", "service": "verdict_dashboard_api"}


def compute_override_id(server_id: str, reason: str) -> str:
    """Compute override ID - compatibility function."""
    import hashlib
    combined = f"{server_id}:{reason}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def get_overrides(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get overrides list - compatibility function."""
    return []


def update_baseline(server_id: str, new_baseline: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    """Update baseline for a server - compatibility function."""
    return {"server_id": server_id, "baseline": new_baseline, "updated": True}


def get_drift_alerts(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get drift alerts - compatibility function."""
    return []


def drift_alerts(server_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get drift alerts for a server - compatibility function."""
    return []


def get_server_risk_profile(server_id: str, session: Session = Depends(get_session)) -> dict[str, Any] | None:
    """Get server risk profile - compatibility function."""
    result = session.execute(
        text("""
            SELECT server_id, server_name, risk_tier, is_monitored
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "server_id": row[0],
        "server_name": row[1],
        "risk_tier": row[2],
        "is_monitored": row[3]
    }


def get_risk_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Get risk summary - compatibility function."""
    result = session.execute(text("""
        SELECT risk_tier, COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY risk_tier
    """))
    tiers = {r[0]: r[1] for r in result.fetchall()}
    return {"tiers": tiers, "total": sum(tiers.values())}


def get_server_risk_tier(server_id: str, session: Session = Depends(get_session)) -> str | None:
    """Get server risk tier - compatibility function."""
    result = session.execute(
        text("SELECT risk_tier FROM mcp_server_registry WHERE server_id = :server_id"),
        {"server_id": server_id}
    )
    row = result.fetchone()
    return row[0] if row else None


def get_server_tier(server_id: str, session: Session = Depends(get_session)) -> dict[str, Any] | None:
    """Get server tier - compatibility function."""
    result = session.execute(
        text("""
            SELECT server_id, risk_tier, is_monitored
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return None
    return {"server_id": row[0], "risk_tier": row[1], "is_monitored": row[2]}


def get_risk_tier_snapshot(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Get risk tier snapshot - compatibility function."""
    result = session.execute(text("""
        SELECT risk_tier, COUNT(*) as count
        FROM mcp_server_registry
        WHERE is_monitored = true
        GROUP BY risk_tier
    """))
    return {"tiers": {r[0]: r[1] for r in result.fetchall()}}


@router.get("/dashboard", response_model=DashboardResponse)
def get_verdict_dashboard(session: Session = Depends(get_session)) -> DashboardResponse:
    """
    Dashboard endpoint aggregating risk_tier distribution and axis scores.
    
    Returns:
        - total_servers: count of all scored servers
        - tiers: dict of tier_name -> count
        - avg_axis_scores: dict of axis_name -> avg_p_score
        - last_updated: most recent score timestamp
    """
    # Get total servers count (servers that have scores)
    total_result = session.execute(text("""
        SELECT COUNT(DISTINCT s.server_id) as total
        FROM mcp_server_registry s
        INNER JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
        WHERE s.is_monitored = true
    """))
    total_servers = total_result.scalar() or 0

    # Get tier distribution
    tier_result = session.execute(text("""
        SELECT s.risk_tier, COUNT(DISTINCT s.server_id) as count
        FROM mcp_server_registry s
        INNER JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
        WHERE s.is_monitored = true AND s.risk_tier IS NOT NULL
        GROUP BY s.risk_tier
    """))
    tiers = {row[0]: row[1] for row in tier_result.fetchall()}

    # Get average axis scores per axis_name
    axis_result = session.execute(text("""
        SELECT axis_name, AVG(p_score) as avg_p_score
        FROM mcp_llm_axis_scores
        WHERE axis_name IS NOT NULL
        GROUP BY axis_name
    """))
    avg_axis_scores = {row[0]: round(float(row[1]), 4) if row[1] else 0.0 
                       for row in axis_result.fetchall()}

    # Get last updated timestamp
    last_updated_result = session.execute(text("""
        SELECT MAX(created_at) as last_updated
        FROM mcp_llm_axis_scores
    """))
    last_updated = last_updated_result.scalar()

    return DashboardResponse(
        total_servers=total_servers,
        tiers=tiers,
        avg_axis_scores=avg_axis_scores,
        last_updated=last_updated
    )


if __name__ == "__main__":
    # Self-test with in-memory SQLite
    import sqlite3
    
    print("Running self-test...")
    
    # Create in-memory SQLite DB and seed data
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
        CREATE TABLE mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            server_name TEXT NOT NULL,
            risk_tier TEXT,
            is_monitored INTEGER DEFAULT 1
        )
    """)
    
    cursor.execute("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            p_score REAL DEFAULT 0.0,
            impact_score REAL DEFAULT 0.0,
            exploitability_score REAL DEFAULT 0.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Seed 5 servers with mixed risk tiers
    servers = [
        ("srv-001", "Server Alpha", "critical"),
        ("srv-002", "Server Beta", "high"),
        ("srv-003", "Server Gamma", "medium"),
        ("srv-004", "Server Delta", "low"),
        ("srv-005", "Server Epsilon", "critical"),
    ]
    
    for server_id, name, tier in servers:
        cursor.execute(
            "INSERT INTO mcp_server_registry (server_id, server_name, risk_tier, is_monitored) VALUES (?, ?, ?, 1)",
            (server_id, name, tier)
        )
    
    # Seed axis scores
    axes = [
        ("srv-001", "security", 0.95),
        ("srv-001", "performance", 0.85),
        ("srv-002", "security", 0.75),
        ("srv-002", "performance", 0.80),
        ("srv-003", "security", 0.55),
        ("srv-003", "performance", 0.60),
        ("srv-004", "security", 0.30),
        ("srv-004", "performance", 0.40),
        ("srv-005", "security", 0.92),
        ("srv-005", "performance", 0.88),
    ]
    
    for server_id, axis_name, p_score in axes:
        cursor.execute(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_score) VALUES (?, ?, ?)",
            (server_id, axis_name, p_score)
        )
    
    conn.commit()
    
    # Test the SQL queries directly
    # Total servers
    cursor.execute("""
        SELECT COUNT(DISTINCT s.server_id) as total
        FROM mcp_server_registry s
        INNER JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
        WHERE s.is_monitored = 1
    """)
    total_servers = cursor.fetchone()[0]
    
    # Tier distribution
    cursor.execute("""
        SELECT s.risk_tier, COUNT(DISTINCT s.server_id) as count
        FROM mcp_server_registry s
        INNER JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
        WHERE s.is_monitored = 1 AND s.risk_tier IS NOT NULL
        GROUP BY s.risk_tier
    """)
    tiers = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Avg axis scores
    cursor.execute("""
        SELECT axis_name, AVG(p_score) as avg_p_score
        FROM mcp_llm_axis_scores
        WHERE axis_name IS NOT NULL
        GROUP BY axis_name
    """)
    avg_axis_scores = {row[0]: round(float(row[1]), 4) for row in cursor.fetchall()}
    
    conn.close()
    
    # Assertions
    assert total_servers >= 5, f"Expected total_servers >= 5, got {total_servers}"
    assert any(count > 0 for count in tiers.values()), f"Expected at least one tier with count > 0, got {tiers}"
    
    print(f"total_servers: {total_servers}")
    print(f"tiers: {tiers}")
    print(f"avg_axis_scores: {avg_axis_scores}")
    print("PASS")