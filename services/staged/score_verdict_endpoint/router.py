"""
Score Verdict Endpoint - FastAPI router for server risk verdict
"""
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["verdict"])


# Axis importance weights (sum to 1.0)
AXIS_WEIGHTS = {
    "overall_risk": 0.25,
    "auth_strength": 0.15,
    "capability_breadth": 0.10,
    "data_sensitivity": 0.20,
    "network_egress": 0.10,
    "maintainer_trust": 0.12,
    "exploit_surface": 0.08,
}


class AxisScore(BaseModel):
    """Individual axis scoring data."""
    axis_name: str
    label: str
    p_top: float = Field(ge=0.0, le=1.0)
    p_critical: float = Field(ge=0.0, le=1.0)
    p_danger: float = Field(ge=0.0, le=1.0)
    escalated: bool = False


class VerdictResponse(BaseModel):
    """Complete verdict response for a server."""
    server_id: str
    name: str
    risk_tier: str
    composite_score: float
    axes: List[AxisScore]


def compute_risk_tier(composite_score: float) -> str:
    """Derive risk tier from composite score."""
    if composite_score > 75:
        return "TRUSTED_GENERAL"
    elif composite_score > 60:
        return "TRUSTED_RESEARCH"
    elif composite_score > 45:
        return "ENTERPRISE_CONTROLLED"
    elif composite_score > 30:
        return "CAUTION_LIMITED"
    elif composite_score > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"


def compute_composite_score(axes: List[dict]) -> float:
    """Compute weighted composite score from axis data."""
    if not axes:
        return 0.0
    
    total_score = 0.0
    for axis in axes:
        axis_name = axis.get("axis_name")
        weight = AXIS_WEIGHTS.get(axis_name, 0.0)
        # Score based on p_top (probability of being top-tier safe)
        p_top = axis.get("p_top", 0.0)
        total_score += weight * p_top * 100
    
    return round(total_score, 2)


@router.get("/servers/{server_id}/verdict", response_model=VerdictResponse)
def get_verdict(server_id: str, session: Session = Depends(get_session)) -> VerdictResponse:
    """
    Retrieve the complete risk verdict for a server.
    
    Fetches all 7 axis scores from mcp_llm_axis_scores, joins with
    mcp_server_registry for server metadata, computes composite score
    weighted by axis importance.
    """
    # Query server metadata
    server_query = text("""
        SELECT server_id, name, url
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """)
    server_result = session.execute(server_query, {"server_id": server_id}).fetchone()
    
    if server_result is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    name = server_result[1]
    url = server_result[2]
    
    # Query all axis scores for this server
    axis_query = text("""
        SELECT 
            las.axis_name,
            las.label,
            las.p_top,
            las.p_critical,
            las.p_danger,
            las.escalated
        FROM mcp_llm_axis_scores las
        WHERE las.server_id = :server_id
        ORDER BY las.axis_name
    """)
    axis_results = session.execute(axis_query, {"server_id": server_id}).fetchall()
    
    if not axis_results:
        raise HTTPException(status_code=404, detail=f"No axis scores found for server {server_id}")
    
    # Build axes list
    axes = []
    for row in axis_results:
        axes.append(AxisScore(
            axis_name=row[0],
            label=row[1] or "",
            p_top=float(row[2]) if row[2] is not None else 0.0,
            p_critical=float(row[3]) if row[3] is not None else 0.0,
            p_danger=float(row[4]) if row[4] is not None else 0.0,
            escalated=bool(row[5]) if row[5] is not None else False,
        ))
    
    # Compute composite score
    axis_dicts = [{"axis_name": a.axis_name, "p_top": a.p_top} for a in axes]
    composite_score = compute_composite_score(axis_dicts)
    
    # Derive risk tier
    risk_tier = compute_risk_tier(composite_score)
    
    return VerdictResponse(
        server_id=server_id,
        name=name,
        risk_tier=risk_tier,
        composite_score=composite_score,
        axes=axes,
    )


# =============================================================================
# Self-test with in-memory SQLite
# =============================================================================
if __name__ == "__main__":
    import sqlite3
    from contextlib import contextmanager
    from unittest.mock import patch
    
    print("Running self-test for score_verdict_endpoint...")
    
    def create_inmemory_db():
        """Create test database with seed data."""
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        
        # Create tables
        cursor.execute("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT,
                p_top REAL DEFAULT 0.0,
                p_critical REAL DEFAULT 0.0,
                p_danger REAL DEFAULT 0.0,
                escalated INTEGER DEFAULT 0
            )
        """)
        
        # Seed 2 servers with 7 axis rows each
        servers = [
            ("srv-001", "SafeChat Server", "https://safe.example.com"),
            ("srv-002", "RiskyBot", "https://risky.example.com"),
        ]
        cursor.executemany(
            "INSERT INTO mcp_server_registry VALUES (?, ?, ?)",
            servers
        )
        
        # Axis names in order
        axis_names = [
            "overall_risk", "auth_strength", "capability_breadth",
            "data_sensitivity", "network_egress", "maintainer_trust",
            "exploit_surface"
        ]
        
        # Server 1: High trust scores
        server1_scores = [
            (0.95, 0.02, 0.01),  # overall_risk
            (0.90, 0.05, 0.02),  # auth_strength
            (0.85, 0.08, 0.03),  # capability_breadth
            (0.92, 0.04, 0.02),  # data_sensitivity
            (0.88, 0.06, 0.03),  # network_egress
            (0.94, 0.03, 0.01),  # maintainer_trust
            (0.91, 0.05, 0.02),  # exploit_surface
        ]
        
        # Server 2: Low trust scores
        server2_scores = [
            (0.15, 0.35, 0.45),  # overall_risk
            (0.20, 0.30, 0.40),  # auth_strength
            (0.25, 0.30, 0.35),  # capability_breadth
            (0.18, 0.32, 0.42),  # data_sensitivity
            (0.22, 0.33, 0.38),  # network_egress
            (0.12, 0.38, 0.48),  # maintainer_trust
            (0.20, 0.35, 0.40),  # exploit_surface
        ]
        
        for i, axis_name in enumerate(axis_names):
            scores = server1_scores[i]
            cursor.execute(
                """INSERT INTO mcp_llm_axis_scores 
                   (server_id, axis_name, label, p_top, p_critical, p_danger, escalated)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("srv-001", axis_name, f"Label_{axis_name}", 
                 scores[0], scores[1], scores[2], 0)
            )
            
            scores = server2_scores[i]
            cursor.execute(
                """INSERT INTO mcp_llm_axis_scores 
                   (server_id, axis_name, label, p_top, p_critical, p_danger, escalated)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("srv-002", axis_name, f"Label_{axis_name}", 
                 scores[0], scores[1], scores[2], 1)
            )
        
        conn.commit()
        return conn
    
    @contextmanager
    def mock_get_session():
        """Mock get_session that uses in-memory SQLite."""
        conn = create_inmemory_db()
        try:
            # Create a mock session-like object
            class MockSession:
                def __init__(self, conn):
                    self._conn = conn
                
                def execute(self, query, params=None):
                    # Simple query rewriter for SQLite
                    sql = str(query)
                    # Convert Postgres-style :param to SQLite ? 
                    sql = sql.replace(":server_id", "?")
                    sql = sql.replace(":server_id", "?")  # second occurrence
                    cursor = self._conn.cursor()
                    if params:
                        cursor.execute(sql, list(params.values()))
                    else:
                        cursor.execute(sql)
                    return cursor
                
                def fetchone(self):
                    return self._conn.cursor().fetchone()
                
                def fetchall(self):
                    return self._conn.cursor().fetchall()
                
                def close(self):
                    self._conn.close()
            
            session = MockSession(conn)
            yield session
            session.close()
        except Exception as e:
            conn.close()
            raise
    
    # Patch get_session and run tests
    with patch("app.db.get_session", mock_get_session):
        # Import after patching
        from app.db import get_session as real_get_session
        
        # Test server 1 (high trust)
        with mock_get_session() as session:
            result = get_verdict("srv-001", session)
            
            assert result.server_id == "srv-001", f"Expected srv-001, got {result.server_id}"
            assert result.name == "SafeChat Server", f"Expected SafeChat Server, got {result.name}"
            assert result.risk_tier != "", "risk_tier should be non-empty"
            assert len(result.axes) == 7, f"Expected 7 axes, got {len(result.axes)}"
            assert isinstance(result.composite_score, float), "composite_score should be float"
            
            print(f"Server 1: {result.name}")
            print(f"  Risk Tier: {result.risk_tier}")
            print(f"  Composite Score: {result.composite_score}")
            print(f"  Axes Count: {len(result.axes)}")
        
        # Test server 2 (low trust)
        with mock_get_session() as session:
            result = get_verdict("srv-002", session)
            
            assert result.server_id == "srv-002", f"Expected srv-002, got {result.server_id}"
            assert result.name == "RiskyBot", f"Expected RiskyBot, got {result.name}"
            assert result.risk_tier != "", "risk_tier should be non-empty"
            assert len(result.axes) == 7, f"Expected 7 axes, got {len(result.axes)}"
            
            print(f"Server 2: {result.name}")
            print(f"  Risk Tier: {result.risk_tier}")
            print(f"  Composite Score: {result.composite_score}")
            print(f"  Axes Count: {len(result.axes)}")
        
        # Test 404 for non-existent server
        try:
            with mock_get_session() as session:
                get_verdict("nonexistent", session)
            assert False, "Should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 404
    
    print("\nPASS")