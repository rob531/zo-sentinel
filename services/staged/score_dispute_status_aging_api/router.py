from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

try:
    from .logic import get_score_dispute_status_aging as _get_status_aging
except ImportError:
    from .logic import get_dispute_status_aging as _get_status_aging


router = APIRouter(prefix="/api")


class StatusAgingEntry(BaseModel):
    status: str
    count: int
    oldest_created_at: Optional[datetime] = None
    newest_created_at: Optional[datetime] = None
    avg_age_hours: float
    resolved_count: int


class ReasonCategoryEntry(BaseModel):
    reason_category: str
    count: int


class ScoreDisputeStatusAgingResponse(BaseModel):
    total: int
    by_status: List[StatusAgingEntry]
    reason_categories: List[ReasonCategoryEntry]


@router.get(
    "/disputes/status-aging",
    response_model=ScoreDisputeStatusAgingResponse,
)
def score_dispute_status_aging(
    db: Session = Depends(get_session),
) -> ScoreDisputeStatusAgingResponse:
    return _get_status_aging(db)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=test_engine)
    test_session_factory = Session(bind=test_engine)

    with test_session_factory as db:
        db.add_all(
            [
                McpScoreDispute(
                    server_id=1,
                    submitted_by="alice",
                    proposed_overall_risk=80.0,
                    proposed_axes={"execution": 90},
                    reason_category="policy",
                    explanation="First open dispute",
                    status="OPEN",
                    created_at=datetime(2024, 1, 1, 0, 0, 0),
                ),
                McpScoreDispute(
                    server_id=2,
                    submitted_by="bob",
                    proposed_overall_risk=60.0,
                    proposed_axes={"execution": 70},
                    reason_category="model_quality",
                    explanation="Second open dispute",
                    status="OPEN",
                    created_at=datetime(2024, 1, 2, 0, 0, 0),
                ),
                McpScoreDispute(
                    server_id=3,
                    submitted_by="carol",
                    proposed_overall_risk=40.0,
                    proposed_axes={"execution": 50},
                    reason_category="policy",
                    explanation="Resolved dispute",
                    status="RESOLVED",
                    created_at=datetime(2024, 1, 3, 0, 0, 0),
                    resolved_at=datetime(2024, 1, 4, 0, 0, 0),
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with test_session_factory as db:
            yield db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/status-aging")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert len(payload["by_status"]) == 2
    assert sum(entry["count"] for entry in payload["by_status"]) == 3
    assert {entry["status"]: entry["count"] for entry in payload["by_status"]} == {
        "OPEN": 2,
        "RESOLVED": 1,
    }
    print("PASS")