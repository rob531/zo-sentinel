from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

from .logic import get_dispute_status_ageing


class AgeBucketCounts(BaseModel):
    b0_1d: int
    b1_7d: int
    b7_30d: int
    b30_plus: int


class StatusAgeing(BaseModel):
    status: str
    count: int
    age_buckets: AgeBucketCounts
    oldest_created_at: Optional[str]


class DisputeStatusAgeingResponse(BaseModel):
    total: int
    open_count: int
    missing_admin_note: int
    statuses: List[StatusAgeing]


router = APIRouter(prefix="/api", tags=["disputes"])


@router.get(
    "/disputes/status-ageing",
    response_model=DisputeStatusAgeingResponse,
)
def dispute_status_ageing(
    session: Session = Depends(get_session),
) -> DisputeStatusAgeingResponse:
    return get_dispute_status_ageing(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta

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
    McpScoreDispute.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    now = datetime.utcnow()
    with TestingSession() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-1",
                    submitted_by="reviewer@example.com",
                    proposed_overall_risk=0.8,
                    proposed_axes={"logic": 0.9},
                    reason_category="model_disagreement",
                    explanation="Pending review",
                    status="pending",
                    admin_note=None,
                    created_at=now - timedelta(hours=12),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-2",
                    submitted_by="reviewer@example.com",
                    proposed_overall_risk=0.6,
                    proposed_axes={"security": 0.7},
                    reason_category="model_disagreement",
                    explanation="Open review",
                    status="review",
                    admin_note="Review in progress",
                    created_at=now - timedelta(days=3),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-3",
                    submitted_by="reviewer@example.com",
                    proposed_overall_risk=0.4,
                    proposed_axes={"logic": 0.5},
                    reason_category="model_disagreement",
                    explanation="Resolved review",
                    status="resolved",
                    admin_note="Resolved",
                    created_at=now - timedelta(days=40),
                    resolved_at=now - timedelta(days=35),
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = lambda: session

        with TestClient(test_app) as client:
            response = client.get("/api/disputes/status-ageing")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 3
            assert payload["open_count"] == 2
            assert payload["missing_admin_note"] == 1
            assert payload["statuses"]
            for status in payload["statuses"]:
                assert sum(status["age_buckets"].values()) == status["count"]

    print("PASS")