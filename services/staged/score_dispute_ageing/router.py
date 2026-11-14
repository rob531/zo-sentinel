"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

from .logic import get_dispute_ageing

router = APIRouter(prefix="/api", tags=["score-dispute-ageing"])


class AgeBucket(BaseModel):
    bucket: Literal["0-1d", "1-7d", "7-30d", "30d+"]
    count: int


class ReasonCategoryCount(BaseModel):
    reason_category: str
    count: int


class AgeingTotals(BaseModel):
    total: int
    open: int
    resolved: int
    by_reason_category: list[ReasonCategoryCount] = []
    median_open_age_hours: float | None = None


class DisputeAgeingResponse(BaseModel):
    buckets: list[AgeBucket]
    totals: AgeingTotals
    by_reason_category: list[ReasonCategoryCount] = []


@router.get("/disputes/ageing", response_model=DisputeAgeingResponse)
def get_ageing(db: Session = Depends(get_session)) -> DisputeAgeingResponse:
    result = get_dispute_ageing(db)
    totals = result["totals"]
    by_reason_category = result.get(
        "by_reason_category", totals.get("by_reason_category", [])
    )
    totals = {**totals, "by_reason_category": by_reason_category}
    return DisputeAgeingResponse(
        buckets=result["buckets"],
        totals=totals,
        by_reason_category=by_reason_category,
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine, checkfirst=True)

    def override_get_session():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with Session(engine) as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id="test-server",
                    status="open",
                    reason_category="model_confidence",
                    proposed_overall_risk=0.4,
                    created_at=now,
                    explanation="test dispute",
                    submitted_by="tester",
                ),
                McpScoreDispute(
                    server_id="test-server",
                    status="open",
                    reason_category="model_confidence",
                    proposed_overall_risk=0.5,
                    created_at=now - timedelta(days=3),
                    explanation="test dispute",
                    submitted_by="tester",
                ),
                McpScoreDispute(
                    server_id="test-server",
                    status="open",
                    reason_category="data_quality",
                    proposed_overall_risk=0.6,
                    created_at=now - timedelta(days=40),
                    explanation="test dispute",
                    submitted_by="tester",
                ),
                McpScoreDispute(
                    server_id="test-server",
                    status="resolved",
                    reason_category="data_quality",
                    proposed_overall_risk=0.7,
                    created_at=now - timedelta(days=10),
                    resolved_at=now - timedelta(days=1),
                    explanation="test dispute",
                    submitted_by="tester",
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/disputes/ageing")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert any(
        bucket["bucket"] == "30d+" and bucket["count"] == 1
        for bucket in payload["buckets"]
    ), payload
    assert payload["totals"]["resolved"] == 1, payload
    print("PASS")