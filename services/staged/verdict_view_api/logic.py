"""
Verdict View API - retrieves full verdict with all 7 risk axes for a server.
"""
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Optional
from datetime import datetime

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisScore(BaseModel):
    """Single axis score data."""
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class VerdictResponse(BaseModel):
    """Full verdict response with server info and all axes."""
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    criteria_version: Optional[str]
    axes: dict[str, AxisScore]
    last_assessed: Optional[datetime]


def get_verdict(server_id: str, session: Session) -> Optional[VerdictResponse]:
    """
    Retrieve full verdict for a server including all 7 risk axes.
    
    Axes: overall_risk, auth_strength, capability_breadth, data_sensitivity,
          network_egress, maintainer_trust, exploit_surface
    """
    # Query axis scores joined with server registry
    query = text("""
        SELECT 
            s.server_id,
            s.name,
            a.risk_tier,
            a.criteria_version,
            a.axis_name,
            a.label,
            a.label_index,
            a.p_top,
            a.p_critical,
            a.p_danger,
            a.escalated,
            a.created_at as last_assessed
        FROM mcp_llm_axis_scores a
        JOIN mcp_server_registry s ON a.server_id = s.server_id
        WHERE a.server_id = :server_id
        ORDER BY a.axis_name
    """)
    
    results = session.execute(query, {"server_id": server_id}).fetchall()
    
    if not results:
        return None
    
    # Build axes dict
    axes = {}
    risk_tier = None
    criteria_version = None
    name = None
    last_assessed = None
    
    for row in results:
        axes[row.axis_name] = AxisScore(
            label=row.label,
            label_index=row.label_index,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=row.escalated
        )
        risk_tier = row.risk_tier
        criteria_version = row.criteria_version
        name = row.name
        last_assessed = row.last_assessed
    
    return VerdictResponse(
        server_id=server_id,
        name=name,
        risk_tier=risk_tier,
        verdict=risk_tier,
        criteria_version=criteria_version,
        axes=axes,
        last_assessed=last_assessed
    )


if __name__ == "__main__":
    # Self-test: verify logic using in-memory SQLite
    import tempfile
    import os
    
    print("Running self-test...")
    
    # Create temp SQLite db for self-test
    temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    temp_db.close()
    db_path = temp_db.name
    
    try:
        # Create in-memory tables matching app schema
        engine = create_engine(f"sqlite:///{db_path}")
        
        with engine.connect() as conn:
            # Create mcp_server_registry table
            conn.execute(text("""
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # Create mcp_llm_axis_scores table
            conn.execute(text("""
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    risk_tier TEXT NOT NULL,
                    criteria_version TEXT,
                    axis_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    label_index INTEGER NOT NULL,
                    p_top REAL NOT NULL,
                    p_critical REAL NOT NULL,
                    p_danger REAL NOT NULL,
                    escalated BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
        
        # Create session
        TestSession = sessionmaker(bind=engine)
        test_session = TestSession()
        
        # Define the 7 axes
        AXES = [
            "overall_risk", "auth_strength", "capability_breadth",
            "data_sensitivity", "network_egress", "maintainer_trust",
            "exploit_surface"
        ]
        
        # Seed 3 servers with all 7 axes each
        servers = [
            {"server_id": "srv_001", "name": "Alpha Server", "risk_tier": "high"},
            {"server_id": "srv_002", "name": "Beta Server", "risk_tier": "medium"},
            {"server_id": "srv_003", "name": "Gamma Server", "risk_tier": "low"},
        ]
        
        for server in servers:
            # Insert server
            test_session.execute(
                text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:server_id, :name)"),
                server
            )
            
            # Insert all 7 axes for this server
            for idx, axis_name in enumerate(AXES):
                test_session.execute(
                    text("""
                        INSERT INTO mcp_llm_axis_scores 
                        (server_id, risk_tier, criteria_version, axis_name, label, label_index, 
                         p_top, p_critical, p_danger, escalated, created_at)
                        VALUES (:server_id, :risk_tier, :criteria_version, :axis_name, 
                                :label, :label_index, :p_top, :p_critical, :p_danger, :escalated, :created_at)
                    """),
                    {
                        "server_id": server["server_id"],
                        "risk_tier": server["risk_tier"],
                        "criteria_version": "v1.0",
                        "axis_name": axis_name,
                        "label": f"{axis_name}_label",
                        "label_index": idx,
                        "p_top": 0.3 + (idx * 0.1),
                        "p_critical": 0.2 + (idx * 0.05),
                        "p_danger": 0.15 + (idx * 0.03),
                        "escalated": idx < 2,
                        "created_at": datetime.utcnow()
                    }
                )
        
        test_session.commit()
        
        # Verify each server
        for server in servers:
            response = get_verdict(server["server_id"], test_session)
            
            # Assert 200 equivalent - response exists
            assert response is not None, f"Expected verdict for {server['server_id']}, got None"
            
            # Assert all 7 axes present
            assert len(response.axes) == 7, f"Expected 7 axes for {server['server_id']}, got {len(response.axes)}"
            
            for axis_name in AXES:
                assert axis_name in response.axes, f"Missing axis {axis_name} for {server['server_id']}"
            
            # Assert matching risk_tier
            assert response.risk_tier == server["risk_tier"], \
                f"Expected risk_tier {server['risk_tier']} for {server['server_id']}, got {response.risk_tier}"
            
            # Assert verdict matches risk_tier
            assert response.verdict == response.risk_tier, \
                f"Expected verdict {response.risk_tier} for {server['server_id']}, got {response.verdict}"
        
        test_session.close()
        print("PASS")
        
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)