from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_resolution_watch

router = APIRouter(
    prefix="/api/disputes/resolution-watch",
    tags=["score-dispute-resolution-watch"],
)


class ResolutionWatchResponse(BaseModel):
    days: int
    status_counts: dict[str, int]
    median_resolution_hours: float | None
    overdue_unresolved: int
    oldest_open_id: int | None
    generated_at: datetime


@router.get("", response_model=ResolutionWatchResponse)
def get_score_dispute_resolution_watch(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> ResolutionWatchResponse:
    result: dict[str, Any] = get_resolution_watch(db, days)
    return ResolutionWatchResponse(**result)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpScoreDispute

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_db = TestSession()

    now = datetime.utcnow()
    test_db.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id=1,
                submitted_by=1,
                proposed_overall_risk=75.0,
                reason_category="model_mismatch",
                explanation="First resolved dispute",
                status="approved",
                created_at=now - timedelta(hours=96),
                resolved_at=now - timedelta(hours=48),
            ),
            McpScoreDispute(
                id=2,
                server_id=1,
                submitted_by=1,
                proposed_overall_risk=80.0,
                reason_category="missing_evidence",
                explanation="Second resolved dispute",
                status="rejected",
                created_at=now - timedelta(hours=48),
                resolved_at=now,
            ),
            McpScoreDispute(
                id=3,
                server_id=1,
                submitted_by=1,
                proposed_overall_risk=85.0,
                reason_category="stale_score",
                explanation="Overdue open dispute",
                status="open",
                created_at=now - timedelta(days=40),
                resolved_at=None,
            ),
            McpScoreDispute(
                id=4,
                server_id=1,
                submitted_by=1,
                proposed_overall_risk=90.0,
                reason_category="stale_score",
                explanation="Open dispute without a creation timestamp",
                status="open",
                created_at=None,
                resolved_at=None,
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Any:
        yield test_db

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/disputes/resolution-watch",
        params={"days": 30},
    )
    payload = response.json()

    assert response.status_code == 200
    assert sum(payload["status_counts"].values()) == 4
    assert payload["median_resolution_hours"] == 48
    assert payload["overdue_unresolved"] == 1
    assert payload["oldest_open_id"] == 3

    test_db.close()
    test_engine.dispose()
    print("PASS")