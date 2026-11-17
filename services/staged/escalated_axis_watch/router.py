"""HTTP surface for the escalated axis watch service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_escalation_watchlist

router = APIRouter(prefix="/api", tags=["escalated-axis-watch"])


class Escalation(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated_to: str
    scored_at: datetime
    decision_rule_version: str


class EscalationResponse(BaseModel):
    count: int
    escalations: list[Escalation]


@router.get("/axes/escalations", response_model=EscalationResponse)
def get_escalations(
    axes: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> EscalationResponse:
    result: dict[str, Any] = get_escalation_watchlist(db, axes)
    return EscalationResponse(**result)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    risk_tier="low",
                ),
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="overall_risk",
                    label="critical",
                    p_top=0.91,
                    p_critical=0.95,
                    p_danger=0.88,
                    escalated=True,
                    escalated_to="security",
                    scored_at=now,
                    decision_rule_version="v1",
                    model_version="test",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-2",
                    axis_name="auth_strength",
                    label="high",
                    p_top=0.82,
                    p_critical=0.84,
                    p_danger=0.79,
                    escalated=True,
                    escalated_to="security",
                    scored_at=now - timedelta(minutes=1),
                    decision_rule_version="v1",
                    model_version="test",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-3",
                    axis_name="network_egress",
                    label="moderate",
                    p_top=0.73,
                    p_critical=0.76,
                    p_danger=0.71,
                    escalated=True,
                    escalated_to="operations",
                    scored_at=now - timedelta(minutes=2),
                    decision_rule_version="v1",
                    model_version="test",
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-1",
                    axis_name="maintainer_trust",
                    label="low",
                    p_top=0.22,
                    p_critical=0.11,
                    p_danger=0.19,
                    escalated=False,
                    escalated_to=None,
                    scored_at=now - timedelta(minutes=3),
                    decision_rule_version="v1",
                    model_version="test",
                ),
                McpLlmAxisScore(
                    id=5,
                    server_id="server-2",
                    axis_name="exploit_surface",
                    label="low",
                    p_top=0.18,
                    p_critical=0.09,
                    p_danger=0.15,
                    escalated=False,
                    escalated_to=None,
                    scored_at=now - timedelta(minutes=4),
                    decision_rule_version="v1",
                    model_version="test",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/escalations")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 3
        assert len(payload["escalations"]) == 3
        assert payload["escalations"][0]["server_id"] == "server-1"
        valid_axes = {
            "overall_risk",
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        }
        assert all(item["axis_name"] in valid_axes for item in payload["escalations"])

        filtered = client.get("/api/axes/escalations", params={"axes": "overall_risk"})
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["count"] == 1
        assert filtered_payload["escalations"][0]["axis_name"] == "overall_risk"

    print("PASS")


if __name__ == "__main__":
    _self_test()