from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Any

from app.db import get_session
from app.models import McpLlmAxisScore


def compute_risk_tier(p_top: float, p_critical: float, p_danger: float, label: str) -> str:
    if label == "CRITICAL":
        return "CRITICAL"
    if p_top >= 0.5:
        return "TOP"
    if p_danger >= 0.5:
        return "DANGER"
    if p_critical >= 0.3:
        return "CRITICAL"
    return "NORMAL"


class AxisData(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    tier: str


class RiskAxesResponse(BaseModel):
    server_id: str
    axes: dict[str, AxisData]
    override_tier: str | None


def build_risk_axes(server_id: str, db: Session) -> RiskAxesResponse:
    scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    axes: dict[str, AxisData] = {}
    override_tier: str | None = None
    for score in scores:
        tier = compute_risk_tier(score.p_top, score.p_critical, score.p_danger, score.label)
        if tier == "CRITICAL":
            override_tier = "CRITICAL"
        axes[score.axis_name] = AxisData(
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            tier=tier,
        )
    return RiskAxesResponse(server_id=server_id, axes=axes, override_tier=override_tier)


def create_app() -> FastAPI:
    app = FastAPI(title="risk_axis_scores")

    @app.get("/api/risk/axes/{server_id}", response_model=RiskAxesResponse)
    def get_risk_axes(server_id: str, db: Session = Depends(get_session)) -> RiskAxesResponse:
        return build_risk_axes(server_id, db)

    return app


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)

    seed_data = [
        ("srv1", "security", "NORMAL", 0.1, 0.1, 0.2),
        ("srv1", "reliability", "WARNING", 0.3, 0.2, 0.4),
        ("srv1", "performance", "TOP", 0.7, 0.1, 0.1),
        ("srv1", "scalability", "NORMAL", 0.2, 0.15, 0.3),
        ("srv1", "compliance", "DANGER", 0.1, 0.2, 0.6),
        ("srv1", "maintainability", "INFO", 0.05, 0.1, 0.1),
        ("srv1", "availability", "CRITICAL", 0.2, 0.5, 0.3),
    ]
    for i, (sid, axis, label, p_top, p_critical, p_danger) in enumerate(seed_data):
        db.add(McpLlmAxisScore(
            id=i + 1,
            server_id=sid,
            axis_name=axis,
            label=label,
            label_index=0,
            p_top=p_top,
            p_critical=p_critical,
            p_danger=p_danger,
            probs="[]",
            model_version="v1",
            decision_rule_version="v1",
            adapter_sha256="abc",
            escalated=False,
            escalated_to=None,
            scored_at=None,
        ))
    db.commit()

    app = create_app()

    def override_get_session():
        yield db

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/risk/axes/srv1")
    data = response.json()

    assert response.status_code == 200
    assert data["server_id"] == "srv1"
    assert len(data["axes"]) == 7
    assert "security" in data["axes"]
    assert "reliability" in data["axes"]
    assert "performance" in data["axes"]
    assert "scalability" in data["axes"]
    assert "compliance" in data["axes"]
    assert "maintainability" in data["axes"]
    assert "availability" in data["axes"]
    assert data["override_tier"] == "CRITICAL"
    assert data["axes"]["availability"]["label"] == "CRITICAL"

    print("PASS")