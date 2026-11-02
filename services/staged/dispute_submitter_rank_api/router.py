"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

try:
    from .logic import get_submitter_rankings
except ImportError:
    from .logic import get_dispute_submitter_rankings as get_submitter_rankings

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class SubmitterAggregate(BaseModel):
    submitted_by: str
    disputes_submitted: int
    disputes_resolved: int
    resolution_rate: float
    distinct_servers: int
    top_reason_category: str | None = None


class DisputeSubmittersResponse(BaseModel):
    days: int
    submitters: list[SubmitterAggregate]


@router.get("/submitters", response_model=DisputeSubmittersResponse)
def get_dispute_submitters(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> DisputeSubmittersResponse:
    result: Any = get_submitter_rankings(db, days=days)
    if isinstance(result, dict):
        submitters = result.get("submitters", [])
    else:
        submitters = result

    return DisputeSubmittersResponse(
        days=days,
        submitters=[SubmitterAggregate(**row) for row in submitters],
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
    McpScoreDispute.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        db.add_all(
            [
                McpScoreDispute(
                    id=101,
                    server_id=11,
                    submitted_by="alice",
                    proposed_overall_risk=0.8,
                    reason_category="wrong_score",
                    status="resolved",
                    created_at=now - timedelta(days=2),
                    resolved_at=now - timedelta(days=1),
                    explanation="Incorrect score",
                ),
                McpScoreDispute(
                    id=102,
                    server_id=12,
                    submitted_by="alice",
                    proposed_overall_risk=0.7,
                    reason_category="wrong_score",
                    status="resolved",
                    created_at=now - timedelta(days=3),
                    resolved_at=now - timedelta(days=2),
                    explanation="Incorrect score",
                ),
                McpScoreDispute(
                    id=103,
                    server_id=13,
                    submitted_by="bob",
                    proposed_overall_risk=0.6,
                    reason_category="missing_context",
                    status="open",
                    created_at=now - timedelta(days=4),
                    resolved_at=None,
                    explanation="Missing context",
                ),
                McpScoreDispute(
                    id=104,
                    server_id=14,
                    submitted_by="bob",
                    proposed_overall_risk=0.5,
                    reason_category="old_dispute",
                    status="open",
                    created_at=now - timedelta(days=40),
                    resolved_at=None,
                    explanation="Outside the requested window",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestingSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get("/api/disputes/submitters", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert len(payload["submitters"]) == 2

    submitters = {item["submitted_by"]: item for item in payload["submitters"]}
    assert set(submitters) == {"alice", "bob"}
    assert submitters["alice"]["disputes_submitted"] == 2
    assert submitters["alice"]["disputes_resolved"] == 2
    assert submitters["alice"]["resolution_rate"] == 1.0
    assert submitters["bob"]["disputes_submitted"] == 1
    assert submitters["bob"]["disputes_resolved"] == 0
    assert submitters["bob"]["resolution_rate"] == 0.0
    assert all(item["submitted_by"] != "old" for item in payload["submitters"])

    print("PASS")