from __future__ import annotations

from datetime import datetime
from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import McpScoreDispute

try:
    from .logic import get_dispute_outcomes as _get_dispute_outcomes
except ImportError:
    try:
        from .logic import get_dispute_outcome_rollup as _get_dispute_outcomes
    except ImportError:
        from .logic import get_outcome_rollup as _get_dispute_outcomes


router = APIRouter(prefix="/api")


class StatusOutcome(BaseModel):
    status: str
    count: int
    pct: float


class ReasonOutcome(BaseModel):
    reason_category: str
    count: int


class ResolutionSummary(BaseModel):
    resolved_count: int
    open_count: int
    median_resolution_hours: float | None


class DisputeOutcomeRollupResponse(BaseModel):
    generated_at: datetime
    total: int
    by_status: list[StatusOutcome]
    by_reason: list[ReasonOutcome]
    resolution: ResolutionSummary
    oldest_open_age_days: float | None


@router.get(
    "/disputes/outcomes",
    response_model=DisputeOutcomeRollupResponse,
)
async def get_dispute_outcomes(
    session: Any = Depends(get_session),
) -> DisputeOutcomeRollupResponse:
    result = _get_dispute_outcomes(session)
    if isawaitable(result):
        result = await result
    return DisputeOutcomeRollupResponse.model_validate(result)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, class_=Session)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    submitted_by VARCHAR NOT NULL,
                    proposed_overall_risk FLOAT NOT NULL,
                    reason_category VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id, server_id, submitted_by, proposed_overall_risk,
                    reason_category, status, created_at, resolved_at
                ) VALUES
                    (1, 'server-1', 'analyst-1', 0.8, 'model_quality',
                     'open', '2025-01-01 00:00:00', NULL),
                    (2, 'server-2', 'analyst-2', 0.6, 'evidence',
                     'open', '2025-01-02 00:00:00', NULL),
                    (3, 'server-3', 'analyst-3', 0.4, 'model_quality',
                     'resolved', '2025-01-01 00:00:00', '2025-01-03 00:00:00'),
                    (4, 'server-4', 'analyst-4', 0.2, 'policy',
                     'rejected', '2025-01-04 00:00:00', NULL)
                """
            )
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/outcomes")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4
        assert payload["resolution"]["open_count"] == 2
        assert any(
            item["status"] == "open" and item["count"] == 2
            for item in payload["by_status"]
        )
        assert payload["resolution"]["median_resolution_hours"] == 48

    assert McpScoreDispute.__tablename__ == "mcp_score_disputes"
    print("PASS")


if __name__ == "__main__":
    _self_test()