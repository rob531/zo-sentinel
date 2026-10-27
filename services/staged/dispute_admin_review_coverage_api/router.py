from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from . import logic as _review_coverage_logic
from .logic import *  # noqa: F401,F403

router = APIRouter(prefix="/api")


class ReviewCoverageBacklog(BaseModel):
    open_count: int
    oldest_open_days: float


class ReviewCoverageResponse(BaseModel):
    by_status: dict[str, int]
    by_reason_category: dict[str, int]
    with_admin_note: int
    with_explanation: int
    resolved_count: int
    mean_resolution_hours: Optional[float]
    median_resolution_hours: Optional[float]
    backlog: ReviewCoverageBacklog


def _find_coverage_builder() -> Any:
    preferred_names = (
        "get_dispute_admin_review_coverage",
        "get_admin_review_coverage",
        "get_review_coverage",
        "build_dispute_admin_review_coverage",
        "build_admin_review_coverage",
        "build_review_coverage",
        "compute_review_coverage",
        "summarize_review_coverage",
        "review_coverage_summary",
    )
    module_values = vars(_review_coverage_logic)
    for name in preferred_names:
        candidate = module_values.get(name)
        if callable(candidate):
            return candidate

    candidates = [
        value
        for name, value in module_values.items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", None) == _review_coverage_logic.__name__
        and any(token in name.lower() for token in ("coverage", "review"))
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError("No review-coverage builder is exported by .logic")
    raise RuntimeError("Unable to identify the review-coverage builder exported by .logic")


@router.get(
    "/disputes/review-coverage",
    response_model=ReviewCoverageResponse,
)
async def get_dispute_admin_review_coverage(
    session: Session = Depends(get_session),
) -> Any:
    builder = _find_coverage_builder()
    result = builder(session)
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, Mapping):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    return result


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    base_time = datetime(2025, 1, 1, 12, 0, 0)
    with TestSession() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id, server_id, submitted_by, proposed_overall_risk,
                    proposed_axes, reason_category, explanation, status,
                    admin_note, created_at, resolved_at
                ) VALUES (
                    :id, :server_id, :submitted_by, :risk,
                    :axes, :reason, :explanation, :status,
                    :admin_note, :created_at, :resolved_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-1",
                    "submitted_by": "user-1",
                    "risk": 0.8,
                    "axes": "{}",
                    "reason": "model_disagreement",
                    "explanation": "",
                    "status": "open",
                    "admin_note": None,
                    "created_at": base_time - timedelta(days=1),
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "server_id": "server-2",
                    "submitted_by": "user-2",
                    "risk": 0.7,
                    "axes": "{}",
                    "reason": "model_disagreement",
                    "explanation": "First review",
                    "status": "resolved",
                    "admin_note": "Reviewed",
                    "created_at": base_time,
                    "resolved_at": base_time + timedelta(hours=2),
                },
                {
                    "id": 3,
                    "server_id": "server-3",
                    "submitted_by": "user-3",
                    "risk": 0.6,
                    "axes": "{}",
                    "reason": "missing_evidence",
                    "explanation": "Second review",
                    "status": "resolved",
                    "admin_note": "Reviewed",
                    "created_at": base_time,
                    "resolved_at": base_time + timedelta(hours=4),
                },
                {
                    "id": 4,
                    "server_id": "server-4",
                    "submitted_by": "user-4",
                    "risk": 0.5,
                    "axes": "{}",
                    "reason": "missing_evidence",
                    "explanation": "Third review",
                    "status": "resolved",
                    "admin_note": None,
                    "created_at": base_time,
                    "resolved_at": base_time + timedelta(hours=3),
                },
            ],
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/review-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert sum(payload["by_status"].values()) == 4
        assert payload["with_admin_note"] == 2
        assert payload["median_resolution_hours"] == 3.0

    test_engine.dispose()
    print("PASS")