from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_queue

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class DisputeQueueItem(BaseModel):
    id: int
    server_id: str
    server_name: str | None = None
    current_risk_tier: str | None = None
    proposed_overall_risk: float | None = None
    reason_category: str
    status: str
    age_hours: float
    submitted_by: str


class DisputeQueueResponse(BaseModel):
    count: int
    items: list[DisputeQueueItem]


@router.get("/queue", response_model=DisputeQueueResponse)
def get_queue(
    db: Session = Depends(get_session),
    status: str = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=200),
) -> DisputeQueueResponse:
    result = get_dispute_queue(db, status=status, limit=limit)
    items = result.get("items", [])

    for item in items:
        if "current_risk_tier" not in item and "current_rier" in item:
            item["current_risk_tier"] = item["current_rier"]

    return DisputeQueueResponse(count=result["count"], items=items)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpScoreDispute, McpServerRegistry

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add(
            McpServerRegistry(
                server_id="server-1",
                name="Test Server",
                risk_tier="CAUTION_LIMITED",
            )
        )
        session.add(
            McpScoreDispute(
                id=1,
                server_id="server-1",
                submitted_by="reviewer@example.com",
                proposed_overall_risk=0.8,
                proposed_axes={"security": 0.9},
                reason_category="review",
                explanation="Manual review required",
                status="open",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/queue?status=open&limit=50")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["current_risk_tier"] == "CAUTION_LIMITED"
    assert isinstance(payload["items"][0]["age_hours"], float)
    assert payload["items"][0]["age_hours"] >= 0
    print("PASS")