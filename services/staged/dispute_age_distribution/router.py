"""router.py -- the service's HTTP surface.

Exposes the dispute age distribution endpoint and delegates data access to the
service logic module.
"""
from __future__ import annotations

try:
    from .logic import get_dispute_age_distribution
except ImportError:  # pragma: no cover - supports direct-file self-tests
    from services.staged.dispute_age_distribution.logic import get_dispute_age_distribution

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api", tags=["disputes"])


class AgeBucket(BaseModel):
    bucket: str
    open: int
    closed: int


class ReasonCount(BaseModel):
    reason_category: str
    count: int


class DisputeAgeDistributionResponse(BaseModel):
    total: int
    open: int
    closed: int
    buckets: list[AgeBucket]
    median_open_age_days: float
    by_reason: list[ReasonCount]


@router.get(
    "/disputes/age-distribution",
    response_model=DisputeAgeDistributionResponse,
)
def get_age_distribution(
    db: Session = Depends(get_session),
) -> DisputeAgeDistributionResponse:
    result = get_dispute_age_distribution(db)
    return DisputeAgeDistributionResponse(**result)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = testing_session()

    now = datetime.now(timezone.utc)
    session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id="server-1",
                submitted_by="user-1",
                proposed_overall_risk=0.2,
                proposed_axes={"overall": 0.2},
                reason_category="false_positive",
                explanation="Test dispute",
                status="open",
                created_at=now,
            ),
            McpScoreDispute(
                id=2,
                server_id="server-2",
                submitted_by="user-2",
                proposed_overall_risk=0.4,
                proposed_axes={"overall": 0.4},
                reason_category="misclassification",
                explanation="Test dispute",
                status="open",
                created_at=now - timedelta(days=2),
            ),
            McpScoreDispute(
                id=3,
                server_id="server-3",
                submitted_by="user-3",
                proposed_overall_risk=0.6,
                proposed_axes={"overall": 0.6},
                reason_category="other",
                explanation="Test dispute",
                status="resolved",
                created_at=now - timedelta(days=40),
            ),
            McpScoreDispute(
                id=4,
                server_id="server-4",
                submitted_by="user-4",
                proposed_overall_risk=0.8,
                proposed_axes={"overall": 0.8},
                reason_category="other",
                explanation="Test dispute",
                status="rejected",
                created_at=now - timedelta(days=120),
            ),
        ]
    )
    session.commit()

    def override_get_session() -> Session:
        return session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    response = TestClient(test_app).get("/api/disputes/age-distribution")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["open"] == 2
    assert payload["closed"] == 2
    assert any(
        bucket["bucket"] == "2-7" and bucket["open"] == 1
        for bucket in payload["buckets"]
    )
    assert isinstance(payload["median_open_age_days"], float)
    assert payload["median_open_age_days"] > 0
    print("PASS")


if __name__ == "__main__":
    _run_self_test()