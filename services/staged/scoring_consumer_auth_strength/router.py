"""scoring_consumer_auth_strength router."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring"])


class ServerScore(BaseModel):
    server_id: str
    name: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: Optional[str] = None


class AuthStrengthResponse(BaseModel):
    axis: str
    servers: List[ServerScore]


def compute_auth_strength(session: Session) -> AuthStrengthResponse:
    query = text("""
        SELECT
            sr.server_id,
            sr.name,
            AVG(ax.p_top) as p_top,
            AVG(ax.p_critical) as p_critical,
            AVG(ax.p_danger) as p_danger,
            MAX(ax.escalated) as escalated,
            MAX(ax.scored_at) as scored_at
        FROM mcp_llm_axis_scores ax
        JOIN mcp_server_registry sr ON ax.server_id = sr.server_id
        WHERE ax.axis_name = :axis_name
        GROUP BY sr.server_id, sr.name
        ORDER BY sr.name
    """)
    result = session.execute(query, {"axis_name": "auth_strength"})
    rows = result.fetchall()

    servers = [
        ServerScore(
            server_id=row.server_id,
            name=row.name,
            p_top=row.p_top or 0.0,
            p_critical=row.p_critical or 0.0,
            p_danger=row.p_danger or 0.0,
            escalated=row.escalated or False,
            scored_at=row.scored_at,
        )
        for row in rows
    ]

    return AuthStrengthResponse(axis="auth_strength", servers=servers)


@router.get("/scoring/auth_strength", response_model=AuthStrengthResponse)
def get_auth_strength(session: Session = Depends(get_session)):
    return compute_auth_strength(session)


if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session as SASession
    from sqlalchemy.pool import StaticPool

    def create_mock_store():
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT,
                    registry_source TEXT,
                    trust_score REAL,
                    risk_tier TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    confidence REAL,
                    description TEXT,
                    meta TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    last_scanned TEXT,
                    last_assessed TEXT,
                    scan_count INTEGER
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    adapter_sha256 TEXT,
                    model_version TEXT,
                    decision_rule_version TEXT,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    label TEXT,
                    label_index INTEGER,
                    escalated INTEGER,
                    escalated_to TEXT,
                    scored_at TEXT
                )
            """))
            conn.commit()

            conn.execute(text(
                "INSERT INTO mcp_server_registry (server_id, name) VALUES (:id, :name)"
            ), {"id": "server1", "name": "Test Server Alpha"})
            conn.execute(text(
                "INSERT INTO mcp_server_registry (server_id, name) VALUES (:id, :name)"
            ), {"id": "server2", "name": "Test Server Beta"})
            conn.execute(text(
                "INSERT INTO mcp_server_registry (server_id, name) VALUES (:id, :name)"
            ), {"id": "server3", "name": "Test Server Gamma"})

            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores 
                (server_id, axis_name, p_top, p_critical, p_danger, escalated, scored_at)
                VALUES (:sid, :axis, :pt, :pc, :pd, :esc, :ts)
            """), {"sid": "server1", "axis": "auth_strength", "pt": 0.85, "pc": 0.10, "pd": 0.05, "esc": 0, "ts": "2025-01-15T10:30:00Z"})
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores 
                (server_id, axis_name, p_top, p_critical, p_danger, escalated, scored_at)
                VALUES (:sid, :axis, :pt, :pc, :pd, :esc, :ts)
            """), {"sid": "server2", "axis": "auth_strength", "pt": 0.20, "pc": 0.50, "pd": 0.30, "esc": 1, "ts": "2025-01-15T10:35:00Z"})
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores 
                (server_id, axis_name, p_top, p_critical, p_danger, escalated, scored_at)
                VALUES (:sid, :axis, :pt, :pc, :pd, :esc, :ts)
            """), {"sid": "server3", "axis": "auth_strength", "pt": 0.60, "pc": 0.25, "pd": 0.15, "esc": 0, "ts": "2025-01-15T10:40:00Z"})
            conn.commit()

        return engine

    engine = create_mock_store()
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/scoring/auth_strength")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "servers" in data, "Response missing 'servers' key"
    assert len(data["servers"]) >= 1, f"Expected >= 1 servers, got {len(data['servers'])}"

    for server in data["servers"]:
        assert 0.0 <= server["p_top"] <= 1.0, f"p_top out of range: {server['p_top']}"

    print("PASS")