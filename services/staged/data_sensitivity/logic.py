from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import List
from datetime import datetime

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class DataSensitivityScore(BaseModel):
    server_id: str
    name: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: datetime

    class Config:
        from_attributes = True


@router.get("/data-sensitivity", response_model=List[DataSensitivityScore])
def get_data_sensitivity_scores(
    session: Session = Depends(get_session)
) -> List[DataSensitivityScore]:
    """
    Retrieve per-server data_sensitivity axis scores joined with server metadata.
    """
    query = text("""
        SELECT
            r.server_id,
            r.name,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.escalated,
            s.scored_at
        FROM mcp_llm_axis_scores s
        INNER JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE s.axis_name = :axis_name
        ORDER BY s.scored_at DESC
    """)
    result = session.execute(query, {"axis_name": "data_sensitivity"})
    rows = result.fetchall()
    return [
        DataSensitivityScore(
            server_id=row.server_id,
            name=row.name,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=bool(row.escalated),
            scored_at=row.scored_at,
        )
        for row in rows
    ]


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                trust_score REAL,
                confidence REAL,
                risk_tier TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
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
                scored_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name) VALUES
            ('srv-001', 'Alpha Data'),
            ('srv-002', 'Beta Store'),
            ('srv-003', 'Gamma Vault')
        """))
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores
            (server_id, axis_name, p_top, p_critical, p_danger, escalated, scored_at) VALUES
            ('srv-001', 'data_sensitivity', 0.75, 0.15, 0.10, 0, '2024-01-15T10:00:00'),
            ('srv-002', 'data_sensitivity', 0.30, 0.40, 0.30, 0, '2024-01-15T11:00:00'),
            ('srv-003', 'data_sensitivity', 0.10, 0.20, 0.70, 1, '2024-01-15T12:00:00')
        """))

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    resp = client.get("/api/scoring/data-sensitivity")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert len(data) == 3, f"Expected 3 entries, got {len(data)}"
    p_top_values = [d["p_top"] for d in data]
    assert 0.75 in p_top_values, f"Expected p_top 0.75 in {p_top_values}"
    print("PASS")