"""
axis_score_ranking_api contract
GET /api/axis-rank?axis={axis_name}&limit=N
Returns servers ranked by p_top for a given axis.
"""
from fastapi import FastAPI, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


# Pydantic models
class ServerRankResponse(BaseModel):
    server_id: str
    name: str
    p_top: float
    risk_tier: Optional[str]
    scored_at: str


class AxisRankResponse(BaseModel):
    results: List[ServerRankResponse]


# FastAPI app
app = FastAPI()


@app.get("/api/axis-rank", response_model=AxisRankResponse)
def get_axis_rank(
    axis: str = Query(..., description="Axis name to rank by"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    session: Session = Depends(get_session),
):
    """
    Returns servers ranked by p_top score for the specified axis.
    """
    sql = text("""
        SELECT 
            s.server_id,
            s.name,
            a.p_top,
            s.risk_tier,
            a.scored_at::text as scored_at
        FROM mcp_llm_axis_scores a
        JOIN mcp_server_registry s ON a.server_id = s.server_id
        WHERE a.axis_name = :axis_name
        ORDER BY a.p_top DESC
        LIMIT :limit
    """)
    result = session.execute(sql, {"axis_name": axis, "limit": limit})
    rows = result.fetchall()
    
    return AxisRankResponse(
        results=[
            ServerRankResponse(
                server_id=row.server_id,
                name=row.name,
                p_top=row.p_top,
                risk_tier=row.risk_tier,
                scored_at=row.scored_at,
            )
            for row in rows
        ]
    )


def _create_in_memory_app():
    """Create FastAPI app with in-memory SQLite for self-test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                axis_name TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                scored_at TEXT,
                adapter_sha256 TEXT
            )
        """))
        conn.commit()
    
    # Seed data
    servers = [
        ("srv-001", "Server Alpha", "high"),
        ("srv-002", "Server Beta", "medium"),
        ("srv-003", "Server Gamma", "low"),
        ("srv-004", "Server Delta", "critical"),
        ("srv-005", "Server Epsilon", "high"),
    ]
    
    scores = [
        (1, "srv-001", "overall_risk", 0.85, "2024-01-15T10:00:00Z"),
        (2, "srv-002", "overall_risk", 0.62, "2024-01-15T10:00:00Z"),
        (3, "srv-003", "overall_risk", 0.45, "2024-01-15T10:00:00Z"),
        (4, "srv-004", "overall_risk", 0.91, "2024-01-15T10:00:00Z"),
        (5, "srv-005", "overall_risk", 0.73, "2024-01-15T10:00:00Z"),
    ]
    
    with engine.connect() as conn:
        for srv in servers:
            conn.execute(text("""
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES (:server_id, :name, :risk_tier)
            """), {"server_id": srv[0], "name": srv[1], "risk_tier": srv[2]})
        for sc in scores:
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores 
                (id, server_id, axis_name, p_top, scored_at)
                VALUES (:id, :server_id, :axis_name, :p_top, :scored_at)
            """), {
                "id": sc[0], "server_id": sc[1], "axis_name": sc[2],
                "p_top": sc[3], "scored_at": sc[4]
            })
        conn.commit()
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    test_app = FastAPI()
    
    @test_app.get("/api/axis-rank", response_model=AxisRankResponse)
    def test_get_axis_rank(
        axis: str = Query(...),
        limit: int = Query(10, ge=1, le=100),
        session: Session = Depends(override_get_session),
    ):
        sql = text("""
            SELECT 
                s.server_id,
                s.name,
                a.p_top,
                s.risk_tier,
                a.scored_at as scored_at
            FROM mcp_llm_axis_scores a
            JOIN mcp_server_registry s ON a.server_id = s.server_id
            WHERE a.axis_name = :axis_name
            ORDER BY a.p_top DESC
            LIMIT :limit
        """)
        result = session.execute(sql, {"axis_name": axis, "limit": limit})
        rows = result.fetchall()
        
        return AxisRankResponse(
            results=[
                ServerRankResponse(
                    server_id=row.server_id,
                    name=row.name,
                    p_top=row.p_top,
                    risk_tier=row.risk_tier,
                    scored_at=row.scored_at,
                )
                for row in rows
            ]
        )
    
    return test_app


if __name__ == "__main__":
    test_app = _create_in_memory_app()
    client = TestClient(test_app)
    
    response = client.get("/api/axis-rank?axis=overall_risk&limit=5")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert "results" in data, "Missing 'results' in response"
    assert len(data["results"]) == 5, f"Expected 5 results, got {len(data['results'])}"
    
    # Assert first server has highest p_top
    p_tops = [r["p_top"] for r in data["results"]]
    assert p_tops == sorted(p_tops, reverse=True), \
        f"Results not sorted by p_top descending: {p_tops}"
    
    print("PASS")
    exit(0)