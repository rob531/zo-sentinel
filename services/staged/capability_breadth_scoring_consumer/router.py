"""Capability breadth scoring consumer router."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/servers", tags=["capability_breadth"])


class CapabilityBreadthResponse(BaseModel):
    axis: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    risk_tier: str
    scored_at: datetime


def compute_risk_tier(p_top: float, p_critical: float, p_danger: float) -> str:
    if p_top >= 0.7:
        return "low"
    elif p_top >= 0.4:
        return "medium"
    elif p_critical >= 0.3:
        return "high"
    elif p_danger >= 0.3:
        return "elevated"
    return "unknown"


@router.get("/{server_id}/capability_breadth", response_model=CapabilityBreadthResponse)
def get_capability_breadth(
    server_id: str,
    session: Session = Depends(get_session),
) -> CapabilityBreadthResponse:
    score = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "capability_breadth",
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    if not score:
        return CapabilityBreadthResponse(
            axis="capability_breadth",
            label="unknown",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            risk_tier="unknown",
            scored_at=datetime.utcnow(),
        )
    risk_tier = compute_risk_tier(
        float(score.p_top or 0),
        float(score.p_critical or 0),
        float(score.p_danger or 0),
    )
    return CapabilityBreadthResponse(
        axis="capability_breadth",
        label=score.label or "unknown",
        p_top=float(score.p_top or 0),
        p_critical=float(score.p_critical or 0),
        p_danger=float(score.p_danger or 0),
        risk_tier=risk_tier,
        scored_at=score.scored_at or datetime.utcnow(),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    session = SessionLocal()
    session.execute(
        text(
            "CREATE TABLE mcp_server_registry ("
            "server_id TEXT PRIMARY KEY,"
            "name TEXT,url TEXT,description TEXT,registry_source TEXT,"
            "first_seen TEXT,last_seen TEXT,last_scanned TEXT,"
            "risk_tier TEXT,trust_score REAL,confidence REAL,"
            "verdict TEXT,verdict_reasoning TEXT,scan_count INTEGER,"
            "last_assessed TEXT,meta TEXT"
            ")"
        )
    )
    session.execute(
        text(
            "CREATE TABLE mcp_llm_axis_scores ("
            "id INTEGER PRIMARY KEY,"
            "server_id TEXT,axis_name TEXT,label TEXT,label_index INTEGER,"
            "p_top REAL,p_critical REAL,p_danger REAL,"
            "probs TEXT,model_version TEXT,decision_rule_version TEXT,"
            "adapter_sha256 TEXT,scored_at TEXT,escalated INTEGER,"
            "escalated_to TEXT"
            ")"
        )
    )
    session.execute(
        text(
            "INSERT INTO mcp_server_registry (server_id, name, url) VALUES "
            "('srv1', 'Server One', 'http://srv1.local'),"
            "('srv2', 'Server Two', 'http://srv2.local')"
        )
    )
    session.execute(
        text(
            "INSERT INTO mcp_llm_axis_scores "
            "(server_id, axis_name, label, p_top, p_critical, p_danger, scored_at) VALUES "
            "('srv1', 'capability_breadth', 'high', 0.8, 0.1, 0.05, '2024-01-01T00:00:00'),"
            "('srv2', 'capability_breadth', 'critical', 0.2, 0.6, 0.15, '2024-01-01T00:00:00')"
        )
    )
    session.commit()
    session.close()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    resp = client.get("/api/servers/srv1/capability_breadth")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "axis" in data
    assert "label" in data
    assert "p_top" in data
    assert "p_critical" in data
    assert "p_danger" in data
    assert "risk_tier" in data
    assert "scored_at" in data
    assert data["risk_tier"] == "low"

    resp2 = client.get("/api/servers/srv2/capability_breadth")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["risk_tier"] == "high"

    engine.dispose()
    print("PASS")