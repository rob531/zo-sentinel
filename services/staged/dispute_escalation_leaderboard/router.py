"""HTTP surface for the dispute escalation leaderboard."""
from __future__ import annotations

from datetime import datetime, timedelta
from inspect import Parameter, signature
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

try:
    from .logic import get_escalation_leaderboard as _get_escalation_leaderboard
except ImportError:
    try:
        from .logic import dispute_escalation_leaderboard as _get_escalation_leaderboard
    except ImportError:
        from .logic import escalation_leaderboard as _get_escalation_leaderboard

router = APIRouter(prefix="/api", tags=["disputes"])


class LeaderEntry(BaseModel):
    server_id: str
    name: str | None
    open: int
    resolved: int
    median_resolution_hours: float | None
    top_reason_category: str | None


class EscalationLeaderboardResponse(BaseModel):
    leaders: list[LeaderEntry]


@router.get(
    "/disputes/escalation-leaderboard",
    response_model=EscalationLeaderboardResponse,
)
def get_escalation_leaderboard_route(
    days: int = Query(30, ge=1, le=365),
    tier: Literal["low", "medium", "high", "critical"] | None = Query(default=None),
    db: Session = Depends(get_session),
) -> dict:
    parameters = signature(_get_escalation_leaderboard).parameters
    accepts_kwargs = any(
        parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs: dict[str, object] = {}
    if accepts_kwargs or "days" in parameters:
        kwargs["days"] = days
    if accepts_kwargs or "tier" in parameters:
        kwargs["tier"] = tier
    return _get_escalation_leaderboard(db, **kwargs)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-a", name="Server A"),
                McpServerRegistry(server_id="server-b", name="Server B"),
                McpServerRegistry(server_id="server-c", name="Server C"),
            ]
        )
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-a",
                    submitted_by="tester",
                    explanation="First open dispute",
                    proposed_overall_risk=0.8,
                    reason_category="security",
                    status="open",
                    created_at=now,
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-a",
                    submitted_by="tester",
                    explanation="Second open dispute",
                    proposed_overall_risk=0.9,
                    reason_category="security",
                    status="open",
                    created_at=now,
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-a",
                    submitted_by="tester",
                    explanation="Resolved dispute",
                    proposed_overall_risk=0.7,
                    reason_category="quality",
                    status="resolved",
                    created_at=now - timedelta(hours=4),
                    resolved_at=now - timedelta(hours=2),
                ),
                McpScoreDispute(
                    id=4,
                    server_id="server-b",
                    submitted_by="tester",
                    explanation="Another resolved dispute",
                    proposed_overall_risk=0.6,
                    reason_category="quality",
                    status="resolved",
                    created_at=now - timedelta(hours=3),
                    resolved_at=now - timedelta(hours=1),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/disputes/escalation-leaderboard",
            params={"days": 365},
        )
        assert response.status_code == 200, response.text
        leaders = response.json()["leaders"]
        server_a = next(
            leader for leader in leaders if leader["server_id"] == "server-a"
        )
        assert server_a["open"] == 2
        assert server_a["median_resolution_hours"] is None or isinstance(
            server_a["median_resolution_hours"], float
        )

    print("PASS")