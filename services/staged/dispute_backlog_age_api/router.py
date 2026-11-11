from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_dispute_backlog_age as get_dispute_backlog_age_data

router = APIRouter(prefix="/api", tags=["dispute-backlog-age"])


class DisputeBacklogAgeBucket(BaseModel):
    bucket: Literal["0-7d", "8-30d", "31-90d", "90d+"]
    count: int


class DisputeBacklogAgeResponse(BaseModel):
    buckets: list[DisputeBacklogAgeBucket]
    total_open: int
    oldest_open_days: int
    by_reason_category: dict[str, int]


@router.get(
    "/disputes/backlog-age",
    response_model=DisputeBacklogAgeResponse,
)
def dispute_backlog_age(
    session: Session = Depends(get_session),
) -> DisputeBacklogAgeResponse:
    payload = get_dispute_backlog_age_data(session)
    return DisputeBacklogAgeResponse.model_validate(payload)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id=1,
                    submitted_by=1,
                    proposed_overall_risk=0.5,
                    reason_category="risk",
                    status="OPEN",
                    explanation="Two-day-old open dispute",
                    created_at=now - timedelta(days=2),
                ),
                McpScoreDispute(
                    server_id=2,
                    submitted_by=1,
                    proposed_overall_risk=0.6,
                    reason_category="risk",
                    status="IN_REVIEW",
                    explanation="Sixty-day-old dispute under review",
                    created_at=now - timedelta(days=60),
                ),
                McpScoreDispute(
                    server_id=3,
                    submitted_by=1,
                    proposed_overall_risk=0.7,
                    reason_category="classification",
                    status="RESOLVED",
                    explanation="Resolved dispute",
                    created_at=now - timedelta(days=10),
                    resolved_at=now - timedelta(days=1),
                ),
                McpScoreDispute(
                    server_id=4,
                    submitted_by=1,
                    proposed_overall_risk=0.9,
                    reason_category="risk",
                    status="OPEN",
                    explanation="Two-hundred-day-old open dispute",
                    created_at=now - timedelta(days=200),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/backlog-age")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_open"] == 3, payload
        assert payload["buckets"][3]["bucket"] == "90d+", payload
        assert payload["buckets"][3]["count"] == 1, payload
        assert payload["oldest_open_days"] >= 200, payload

    print("PASS")