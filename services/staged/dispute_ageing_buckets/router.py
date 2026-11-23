"""HTTP surface for dispute ageing bucket reporting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

from .logic import get_ageing_buckets

router = APIRouter(prefix="/api", tags=["dispute-ageing-buckets"])


class AgeingBucket(BaseModel):
    bucket: str
    count: int


class AgeingBucketsResponse(BaseModel):
    buckets: list[AgeingBucket]
    by_status: dict[str, int]
    oldest_open_dispute_id: int | None
    total_open: int


@router.get(
    "/disputes/ageing-buckets",
    response_model=AgeingBucketsResponse,
)
def get_dispute_ageing_buckets(
    db: Session = Depends(get_session),
) -> AgeingBucketsResponse:
    return AgeingBucketsResponse(**get_ageing_buckets(db))


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with testing_session() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-1",
                    status="OPEN",
                    reason_category="test",
                    proposed_overall_risk=0.2,
                    explanation="Test dispute",
                    submitted_by="test-user",
                    created_at=now - timedelta(days=2),
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-2",
                    status="OPEN",
                    reason_category="test",
                    proposed_overall_risk=0.4,
                    explanation="Test dispute",
                    submitted_by="test-user",
                    created_at=now - timedelta(days=40),
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-3",
                    status="RESOLVED",
                    reason_category="test",
                    proposed_overall_risk=0.6,
                    explanation="Test dispute",
                    submitted_by="test-user",
                    created_at=now - timedelta(days=5),
                    resolved_at=now,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/ageing-buckets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_open"] == 2
        assert next(
            bucket["count"]
            for bucket in payload["buckets"]
            if bucket["bucket"] == "31-90"
        ) == 1

    print("PASS")