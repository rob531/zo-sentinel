from fastapi import FastAPI, Depends
from pydantic import BaseModel
from typing import List
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import MetaData

test_data = [
    {"id": 1, "server_id": 1, "scored_at": "2024-01-01T00:00:00", "p_top": 0.80, "p_critical": 0.10, "p_danger": 0.10, "axis_name": "test", "adapter_sha256": "abc", "label": "test", "label_index": 0, "model_version": "v1", "decision_rule_version": "v1", "escalated": False, "escalated_to": None, "probs": "[]"},
    {"id": 2, "server_id": 1, "scored_at": "2024-01-02T00:00:00", "p_top": 0.55, "p_critical": 0.20, "p_danger": 0.25, "axis_name": "test", "adapter_sha256": "abc", "label": "test", "label_index": 0, "model_version": "v1", "decision_rule_version": "v1", "escalated": False, "escalated_to": None, "probs": "[]"},
    {"id": 3, "server_id": 1, "scored_at": "2024-01-03T00:00:00", "p_top": 0.25, "p_critical": 0.30, "p_danger": 0.45, "axis_name": "test", "adapter_sha256": "abc", "label": "test", "label_index": 0, "model_version": "v1", "decision_rule_version": "v1", "escalated": False, "escalated_to": None, "probs": "[]"},
    {"id": 4, "server_id": 2, "scored_at": "2024-01-01T00:00:00", "p_top": 0.75, "p_critical": 0.10, "p_danger": 0.15, "axis_name": "test", "adapter_sha256": "def", "label": "test", "label_index": 0, "model_version": "v1", "decision_rule_version": "v1", "escalated": False, "escalated_to": None, "probs": "[]"},
    {"id": 5, "server_id": 2, "scored_at": "2024-01-02T00:00:00", "p_top": 0.50, "p_critical": 0.25, "p_danger": 0.25, "axis_name": "test", "adapter_sha256": "def", "label": "test", "label_index": 0, "model_version": "v1", "decision_rule_version": "v1", "escalated": False, "escalated_to": None, "probs": "[]"},
    {"id": 6, "server_id": 2, "scored_at": "2024-01-03T00:00:00", "p_top": 0.20, "p_critical": 0.35, "p_danger": 0.45, "axis_name": "test", "adapter_sha256": "def", "label": "test", "label_index": 0, "model_version": "v1", "decision_rule_version": "v1", "escalated": False, "escalated_to": None, "probs": "[]"},
]

engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
metadata = MetaData()
metadata.reflect(bind=engine)
with engine.connect() as conn:
    conn.execute(text("DROP TABLE IF EXISTS mcp_llm_axis_scores"))
    conn.execute(text("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            scored_at TIMESTAMP NOT NULL,
            p_top FLOAT NOT NULL,
            p_critical FLOAT NOT NULL,
            p_danger FLOAT NOT NULL,
            axis_name VARCHAR(100),
            adapter_sha256 VARCHAR(64),
            label VARCHAR(100),
            label_index INTEGER,
            model_version VARCHAR(50),
            decision_rule_version VARCHAR(50),
            escalated BOOLEAN,
            escalated_to VARCHAR(100),
            probs TEXT
        )
    """))
    for row in test_data:
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores 
            (id, server_id, scored_at, p_top, p_critical, p_danger, axis_name, adapter_sha256, label, label_index, model_version, decision_rule_version, escalated, escalated_to, probs)
            VALUES (:id, :server_id, :scored_at, :p_top, :p_critical, :p_danger, :axis_name, :adapter_sha256, :label, :label_index, :model_version, :decision_rule_version, :escalated, :escalated_to, :probs)
        """), row)
    conn.commit()

SessionLocal = sessionmaker(bind=engine)

def mock_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ScoreEntry(BaseModel):
    scored_at: datetime
    p_top: float
    p_critical: float
    p_danger: float
    risk_tier: str

    class Config:
        from_attributes = True

class ServerScoreHistoryResponse(BaseModel):
    server_id: int
    series: List[ScoreEntry]

def risk_tier_from_p_top(p_top: float) -> str:
    if p_top >= 0.75:
        return "TRUSTED_GENERAL"
    elif p_top >= 0.60:
        return "TRUSTED_RESEARCH"
    elif p_top >= 0.45:
        return "ENTERPRISE_CONTROLLED"
    elif p_top >= 0.30:
        return "CAUTION_LIMITED"
    elif p_top >= 0.15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

def get_score_history(db: Session, server_id: int) -> ServerScoreHistoryResponse:
    results = db.execute(
        text("""
            SELECT scored_at, p_top, p_critical, p_danger
            FROM mcp_llm_axis_scores
            WHERE server_id = :server_id
            ORDER BY scored_at ASC
        """),
        {"server_id": server_id}
    ).fetchall()
    
    series = [
        ScoreEntry(
            scored_at=row.scored_at,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            risk_tier=risk_tier_from_p_top(row.p_top)
        )
        for row in results
    ]
    
    return ServerScoreHistoryResponse(server_id=server_id, series=series)

app = FastAPI()

@app.get("/api/servers/{server_id}/score-history", response_model=ServerScoreHistoryResponse)
def get_server_score_history(server_id: int, db: Session = Depends(mock_session)):
    return get_score_history(db, server_id)

from app.db import get_session
app.dependency_overrides[get_session] = mock_session

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    r1 = client.get("/api/servers/1/score-history")
    assert r1.status_code == 200, f"Expected 200, got {r1.status_code}"
    data1 = r1.json()
    assert len(data1["series"]) == 3
    assert data1["series"][0]["risk_tier"] == "TRUSTED_GENERAL"
    assert data1["series"][1]["risk_tier"] == "ENTERPRISE_CONTROLLED"
    assert data1["series"][2]["risk_tier"] == "HIGH_RISK_ISOLATED"
    
    r2 = client.get("/api/servers/2/score-history")
    assert r2.status_code == 200
    data2 = r2.json()
    assert len(data2["series"]) == 3
    assert data2["series"][0]["risk_tier"] == "TRUSTED_GENERAL"
    assert data2["series"][1]["risk_tier"] == "ENTERPRISE_CONTROLLED"
    assert data2["series"][2]["risk_tier"] == "HIGH_RISK_ISOLATED"
    
    print("PASS")