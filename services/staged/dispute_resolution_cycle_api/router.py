"""HTTP surface for dispute resolution-cycle reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_resolution_cycle

router = APIRouter(prefix="/api", tags=["disputes"])


class ResolutionTotals(BaseModel):
    submitted: int
    resolved: int
    open: int
    median_resolution_hours: float | None
    p90_resolution_hours: float | None


class ResolutionByStatus(BaseModel):
    status: str
    count: int
    oldest_created_at: datetime | None


class ResolutionByReasonCategory(BaseModel):
    reason_category: str
    count: int


class AgingOpenDispute(BaseModel):
    server_id: int
    status: str
    created_at: datetime
    age_days: float


class ResolutionCycleResponse(BaseModel):
    days: int
    generated_at: datetime
    totals: ResolutionTotals
    by_status: list[ResolutionByStatus]
    by_reason_category: list[ResolutionByReasonCategory]
    aging_open: list[AgingOpenDispute]


@router.get(
    "/disputes/resolution-cycle",
    response_model=ResolutionCycleResponse,
)
def resolution_cycle(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ResolutionCycleResponse:
    report: Any = get_resolution_cycle(db, days)
    return ResolutionCycleResponse.parse_obj(report)


def _self_test() -> None:
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpScoreDispute

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.utcnow()
    rows = [
        McpScoreDispute(
            id=1,
            server_id=101,
            submitted_by="analyst-1",
            proposed_overall_risk=0.8,
            explanation="First seeded dispute",
            reason_category="scoring",
            status="resolved",
            created_at=now - timedelta(days=3),
            resolved_at=now - timedelta(days=3) + timedelta(hours=2),
        ),
        McpScoreDispute(
            id=2,
            server_id=102,
            submitted_by="analyst-2",
            proposed_overall_risk=0.6,
            explanation="Second seeded dispute",
            reason_category="policy",
            status="resolved",
            created_at=now - timedelta(days=2),
            resolved_at=now - timedelta(days=2) + timedelta(hours=6),
        ),
        McpScoreDispute(
            id=3,
            server_id=103,
            submitted_by="analyst-3",
            proposed_overall_risk=0.4,
            explanation="Open seeded dispute",
            reason_category="scoring",
            status="open",
            created_at=now - timedelta(days=1),
        ),
    ]
    testing_session.add_all(rows)
    testing_session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: testing_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/resolution-cycle", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["totals"]["submitted"] == 3
        assert payload["totals"]["resolved"] == 2
        assert len(payload["aging_open"]) == 1
        assert payload["totals"]["median_resolution_hours"] == 4.0

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()