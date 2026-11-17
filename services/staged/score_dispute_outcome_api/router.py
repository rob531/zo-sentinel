from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

from .logic import get_dispute_outcomes as _get_dispute_outcomes


router = APIRouter(prefix="/api", tags=["score-dispute-outcome"])


class ReasonOutcome(BaseModel):
    reason_category: str
    count: int
    mean_resolution_hours: Optional[float] = None


class DisputeOutcomeResponse(BaseModel):
    days: int
    status_counts: Dict[str, int]
    mean_resolution_hours: Optional[float] = None
    resolved_count: int
    open_count: int
    by_reason: List[ReasonOutcome]


@router.get("/disputes/outcomes", response_model=DisputeOutcomeResponse)
def get_dispute_outcomes(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> DisputeOutcomeResponse:
    return _get_dispute_outcomes(session=session, days=days)


def _self_test() -> None:
    from fastapi.testclient import TestClient

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
            server_id="server-alpha",
            submitted_by="alice",
            proposed_overall_risk="high",
            proposed_axes={"malware": 0.9},
            reason_category="model_conflict",
            explanation="First resolved dispute",
            status="resolved",
            admin_note="Accepted",
            created_at=now - timedelta(hours=4),
            resolved_at=now - timedelta(hours=3),
        ),
        McpScoreDispute(
            id=2,
            server_id="server-beta",
            submitted_by="bob",
            proposed_overall_risk="medium",
            proposed_axes={"exploit": 0.6},
            reason_category="missing_context",
            explanation="Second resolved dispute",
            status="resolved",
            admin_note="Accepted",
            created_at=now - timedelta(hours=7),
            resolved_at=now - timedelta(hours=4),
        ),
        McpScoreDispute(
            id=3,
            server_id="server-gamma",
            submitted_by="carol",
            proposed_overall_risk="low",
            proposed_axes={"phishing": 0.2},
            reason_category="billing",
            explanation="Open dispute",
            status="open",
            created_at=now - timedelta(hours=2),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=4,
            server_id="server-delta",
            submitted_by="dave",
            proposed_overall_risk="critical",
            proposed_axes={"c2": 0.95},
            reason_category="data_quality",
            explanation="Pending dispute",
            status="pending",
            created_at=now - timedelta(hours=1),
            resolved_at=None,
        ),
    ]
    testing_session.add_all(rows)
    testing_session.commit()

    def override_get_session():
        yield testing_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/disputes/outcomes", params={"days": 30})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["resolved_count"] == 2, payload
            assert payload["mean_resolution_hours"] == 2.0, payload
            assert payload["status_counts"]["open"] == 1, payload
            assert payload["status_counts"]["pending"] == 1, payload
            assert payload["open_count"] == 2, payload
            reason_counts = {
                item["reason_category"]: item["count"] for item in payload["by_reason"]
            }
            assert reason_counts["billing"] == 1, payload
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()