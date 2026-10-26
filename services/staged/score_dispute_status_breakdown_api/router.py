from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

from .logic import get_dispute_status_breakdown

router = APIRouter(prefix="/api", tags=["score-disputes"])


class StatusCount(BaseModel):
    status: Optional[str]
    count: int


class ReasonCount(BaseModel):
    reason_category: Optional[str]
    count: int


class StatusBreakdownResponse(BaseModel):
    total: int
    by_status: list[StatusCount]
    by_reason: list[ReasonCount]
    median_resolution_hours: Optional[float]


@router.get(
    "/disputes/status-breakdown",
    response_model=StatusBreakdownResponse,
    summary="Get score-dispute counts by status and reason",
)
async def score_dispute_status_breakdown(
    session: Session = Depends(get_session),
) -> StatusBreakdownResponse:
    result = get_dispute_status_breakdown(session)
    if isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    resolved_at = created_at + timedelta(hours=48)

    with TestingSession() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id=1,
                    submitted_by="contract-user",
                    proposed_overall_risk=0.8,
                    proposed_axes={},
                    reason_category="model_conflict",
                    status="open",
                    explanation="Pending review",
                    admin_note=None,
                    created_at=created_at,
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by="contract-user",
                    proposed_overall_risk=0.7,
                    proposed_axes={},
                    reason_category="model_conflict",
                    status="open",
                    explanation="Pending review",
                    admin_note=None,
                    created_at=created_at,
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id=2,
                    submitted_by="contract-user",
                    proposed_overall_risk=0.6,
                    proposed_axes={},
                    reason_category="data_quality",
                    status="resolved",
                    explanation="Resolved",
                    admin_note="Accepted",
                    created_at=created_at,
                    resolved_at=resolved_at,
                ),
                McpScoreDispute(
                    server_id=3,
                    submitted_by="contract-user",
                    proposed_overall_risk=0.4,
                    proposed_axes={},
                    reason_category="data_quality",
                    status="resolved",
                    explanation="Resolved",
                    admin_note="Accepted",
                    created_at=created_at,
                    resolved_at=resolved_at,
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
        response = client.get("/api/disputes/status-breakdown")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert {"status": "open", "count": 2} in payload["by_status"]
        assert {"status": "resolved", "count": 2} in payload["by_status"]
        assert payload["median_resolution_hours"] == 48.0

    print("PASS")