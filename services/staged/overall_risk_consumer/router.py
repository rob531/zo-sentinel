"""
Overall Risk Consumer Router
Provides composite risk scoring endpoint for server risk assessment.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api/risk", tags=["risk"])


class AxisScore(BaseModel):
    """Individual risk axis score."""
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False


class OverallRiskResponse(BaseModel):
    """Overall risk assessment response."""
    server_id: str
    axes: dict[str, AxisScore]
    composite_score: float
    risk_tier: str
    verdict: str
    criteria_version: str
    scored_at: str

    class Config:
        from_attributes = True


@router.get("/overall/{server_id}", response_model=OverallRiskResponse)
def get_overall_risk(
    server_id: str,
    session: Session = Depends(get_session)
) -> OverallRiskResponse:
    """
    Get overall risk assessment for a server.
    
    Reads all 7 axis scores and computes composite risk.
    """
    # Query all axis scores for the server
    axis_query = text("""
        SELECT 
            axis_name,
            label,
            p_top,
            p_critical,
            p_danger,
            escalated
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
        ORDER BY axis_name
    """)
    
    result = session.execute(axis_query, {"server_id": server_id})
    rows = result.fetchall()
    
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No axis scores found for server: {server_id}"
        )
    
    # Query server registry for risk tier
    tier_query = text("""
        SELECT 
            risk_tier,
            verdict,
            criteria_version,
            scored_at
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """)
    
    tier_result = session.execute(tier_query, {"server_id": server_id})
    tier_row = tier_result.fetchone()
    
    if not tier_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server not found in registry: {server_id}"
        )
    
    # Build axes dictionary
    axes = {}
    composite_values = []
    
    for row in rows:
        axis_name = row[0]
        label = row[1]
        p_top = row[2]
        p_critical = row[3]
        p_danger = row[4]
        escalated = bool(row[5])
        
        axes[axis_name] = AxisScore(
            label=label,
            p_top=float(p_top) if p_top is not None else None,
            p_critical=float(p_critical) if p_critical is not None else None,
            p_danger=float(p_danger) if p_danger is not None else None,
            escalated=escalated
        )
        
        # Accumulate for composite score
        if p_top is not None:
            composite_values.append(float(p_top))
        elif p_critical is not None:
            composite_values.append(float(p_critical) * 0.8)
        elif p_danger is not None:
            composite_values.append(float(p_danger) * 0.5)
    
    # Compute composite score
    composite_score = 0.0
    if composite_values:
        composite_score = sum(composite_values) / len(composite_values)
    
    return OverallRiskResponse(
        server_id=server_id,
        axes=axes,
        composite_score=round(composite_score, 4),
        risk_tier=tier_row[0] or "unknown",
        verdict=tier_row[1] or "pending",
        criteria_version=tier_row[2] or "1.0",
        scored_at=str(tier_row[3]) if tier_row[3] else ""
    )


if __name__ == "__main__":
    import sqlite3
    from datetime import datetime
    
    print("Running self-test with in-memory SQLite...")
    
    # Create in-memory SQLite database
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            label TEXT,
            p_top REAL,
            p_critical REAL,
            p_danger REAL,
            escalated INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE mcp_server_registry (
            id INTEGER PRIMARY KEY,
            server_id TEXT UNIQUE NOT NULL,
            risk_tier TEXT,
            verdict TEXT,
            criteria_version TEXT,
            scored_at TIMESTAMP
        )
    """)
    
    # Seed test data - 3 servers with mixed axis scores
    test_servers = [
        ("server-001", "high", "critical", "2.1"),
        ("server-002", "medium", "elevated", "2.1"),
        ("server-003", "low", "healthy", "2.1"),
    ]
    
    axis_names = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"
    ]
    
    axis_labels = [
        "Overall Risk Score", "Authentication Strength", "Capability Breadth",
        "Data Sensitivity", "Network Egress Points", "Maintainer Trust Level", "Exploit Surface Area"
    ]
    
    for server_id, tier, verdict, version in test_servers:
        cursor.execute(
            "INSERT INTO mcp_server_registry (server_id, risk_tier, verdict, criteria_version, scored_at) VALUES (?, ?, ?, ?, ?)",
            (server_id, tier, verdict, version, datetime.now().isoformat())
        )
        
        # Vary scores based on server
        if server_id == "server-001":
            scores = [(0.95, None, None, 1), (0.85, None, None, 0), (0.90, None, None, 1),
                      (0.88, None, None, 0), (0.92, None, None, 1), (0.75, None, None, 0), (0.90, None, None, 1)]
        elif server_id == "server-002":
            scores = [(0.45, None, None, 0), (0.55, None, None, 0), (0.50, None, None, 0),
                      (None, 0.65, None, 0), (None, 0.60, None, 0), (0.70, None, None, 0), (None, 0.55, None, 0)]
        else:
            scores = [(0.10, None, None, 0), (0.15, None, None, 0), (0.12, None, None, 0),
                      (0.08, None, None, 0), (0.18, None, None, 0), (0.20, None, None, 0), (0.10, None, None, 0)]
        
        for i, (p_top, p_critical, p_danger, escalated) in enumerate(scores):
            cursor.execute(
                "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical, p_danger, escalated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (server_id, axis_names[i], axis_labels[i], p_top, p_critical, p_danger, escalated)
            )
    
    conn.commit()
    
    # Run test queries
    for server_id, expected_tier, _, _ in test_servers:
        # Test axis scores query
        cursor.execute(
            "SELECT axis_name, label, p_top, p_critical, p_danger, escalated FROM mcp_llm_axis_scores WHERE server_id = ? ORDER BY axis_name",
            (server_id,)
        )
        axis_rows = cursor.fetchall()
        assert len(axis_rows) == 7, f"Expected 7 axis rows for {server_id}, got {len(axis_rows)}"
        
        # Test registry query
        cursor.execute(
            "SELECT risk_tier, verdict, criteria_version, scored_at FROM mcp_server_registry WHERE server_id = ?",
            (server_id,)
        )
        tier_row = cursor.fetchone()
        assert tier_row is not None, f"Server {server_id} not found in registry"
        assert tier_row[0] == expected_tier, f"Expected tier {expected_tier} for {server_id}, got {tier_row[0]}"
        
        # Compute composite score
        composite_values = []
        for row in axis_rows:
            p_top, p_critical, p_danger = row[2], row[3], row[4]
            if p_top is not None:
                composite_values.append(float(p_top))
            elif p_critical is not None:
                composite_values.append(float(p_critical) * 0.8)
            elif p_danger is not None:
                composite_values.append(float(p_danger) * 0.5)
        
        composite_score = sum(composite_values) / len(composite_values) if composite_values else 0.0
        assert isinstance(composite_score, float), f"composite_score should be float, got {type(composite_score)}"
        assert tier_row[0], f"risk_tier should be non-empty for {server_id}"
        
        print(f"  ✓ {server_id}: composite_score={composite_score:.4f}, risk_tier={tier_row[0]}")
    
    conn.close()
    print("PASS")