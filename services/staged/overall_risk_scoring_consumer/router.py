from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import datetime

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter()


class OverallRiskResponse(BaseModel):
    server_id: str
    server_name: str
    p_top: float
    p_critical: float
    p_danger: float
    label: str
    label_index: int
    scored_at: datetime.datetime
    decision_rule_version: str
    model_version: str

    class Config:
        from_attributes = True


def get_overall_risk(server_id: str, db: Session) -> OverallRiskResponse | None:
    stmt = (
        text("""
            SELECT
                s.server_id,
                s.name AS server_name,
                a.p_top,
                a.p_critical,
                a.p_danger,
                a.label,
                a.label_index,
                a.scored_at,
                a.decision_rule_version,
                a.model_version
            FROM mcp_llm_axis_scores a
            JOIN mcp_server_registry s ON a.server_id = s.server_id
            WHERE a.server_id = :server_id
              AND a.axis_name = 'overall_risk'
            ORDER BY a.scored_at DESC
            LIMIT 1
        """)
    )
    row = db.execute(stmt, {"server_id": server_id}).fetchone()
    if not row:
        return None
    return OverallRiskResponse(
        server_id=row.server_id,
        server_name=row.server_name,
        p_top=float(row.p_top),
        p_critical=float(row.p_critical),
        p_danger=float(row.p_danger),
        label=row.label,
        label_index=row.label_index,
        scored_at=row.scored_at,
        decision_rule_version=row.decision_rule_version,
        model_version=row.model_version,
    )


@router.get("/api/servers/{server_id}/overall-risk", response_model=OverallRiskResponse)
def get_server_overall_risk(server_id: str, db: Session = Depends(get_session)):
    result = get_overall_risk(server_id, db)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Overall risk score not found")
    return result


if __name__ == "__main__":
    that_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    that_session_factory = sessionmaker(bind=that_engine)

    with that_engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER DEFAULT 0,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id SERIAL PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                scored_at TIMESTAMP NOT NULL,
                decision_rule_version TEXT,
                model_version TEXT,
                escalated BOOLEAN DEFAULT FALSE,
                escalated_to TEXT,
                adapter_sha256 TEXT
            )
        """))

    seeded_session = that_session_factory()
    now = datetime.datetime.utcnow()

    seeded_session.execute(text("""
        INSERT INTO mcp_server_registry (server_id, name, url, description, risk_tier, trust_score, confidence, first_seen, last_seen)
        VALUES (:server_id, :name, :url, :description, :risk_tier, :trust_score, :confidence, :first_seen, :last_seen)
    """), {
        "server_id": "srv-test-001",
        "name": "Test Server Alpha",
        "url": "https://test-alpha.example.com",
        "description": "Test server for unit testing",
        "risk_tier": "medium",
        "trust_score": 0.75,
        "confidence": 0.85,
        "first_seen": now,
        "last_seen": now,
    })

    seeded_session.execute(text("""
        INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, p_danger, probs, label, label_index, scored_at, decision_rule_version, model_version, adapter_sha256)
        VALUES (:server_id, :axis_name, :p_top, :p_critical, :p_danger, :probs, :label, :label_index, :scored_at, :decision_rule_version, :model_version, :adapter_sha256)
    """), {
        "server_id": "srv-test-001",
        "axis_name": "overall_risk",
        "p_top": 0.15,
        "p_critical": 0.35,
        "p_danger": 0.50,
        "probs": "[0.15, 0.35, 0.50]",
        "label": "elevated",
        "label_index": 2,
        "scored_at": now,
        "decision_rule_version": "v2.1.0",
        "model_version": "claude-3-5-sonnet",
        "adapter_sha256": "abc123def456",
    })
    seeded_session.commit()
    seeded_session.close()

    def override_get_session():
        session = that_session_factory()
        try:
            yield session
        finally:
            session.close()

    from fastapi import FastAPI
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/servers/srv-test-001/overall-risk")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "server_id" in data, f"Missing server_id in response: {data}"
    assert data["server_id"] == "srv-test-001", f"Expected srv-test-001, got {data['server_id']}"
    assert "server_name" in data, f"Missing server_name in response: {data}"
    assert "scored_at" in data, f"Missing scored_at in response: {data}"
    assert data["scored_at"] is not None, "scored_at must not be null"
    assert isinstance(data["p_top"], float), f"p_top must be float, got {type(data['p_top'])}"
    assert isinstance(data["p_critical"], float), f"p_critical must be float, got {type(data['p_critical'])}"
    assert isinstance(data["p_danger"], float), f"p_danger must be float, got {type(data['p_danger'])}"
    assert 0 <= data["p_top"] <= 1, f"p_top out of range: {data['p_top']}"
    assert 0 <= data["p_critical"] <= 1, f"p_critical out of range: {data['p_critical']}"
    assert 0 <= data["p_danger"] <= 1, f"p_danger out of range: {data['p_danger']}"

    print("PASS")