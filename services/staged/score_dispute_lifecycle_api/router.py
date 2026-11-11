from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_lifecycle_metrics

router = APIRouter(prefix="/api")


@router.get("/disputes/lifecycle")
def score_dispute_lifecycle(session: Session = Depends(get_session)) -> dict:
    return get_lifecycle_metrics(session)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    rows = [
        McpScoreDispute(
            id=1,
            server_id=101,
            submitted_by=1,
            proposed_overall_risk=0.8,
            proposed_axes={"availability": 0.8},
            reason_category="data_quality",
            explanation="Open dispute one",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=1),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=2,
            server_id=102,
            submitted_by=1,
            proposed_overall_risk=0.7,
            proposed_axes={"integrity": 0.7},
            reason_category="model_variance",
            explanation="Open dispute two",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=2),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=3,
            server_id=103,
            submitted_by=2,
            proposed_overall_risk=0.6,
            proposed_axes={"confidentiality": 0.6},
            reason_category="data_quality",
            explanation="Oldest open dispute",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=100),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=4,
            server_id=104,
            submitted_by=1,
            proposed_overall_risk=0.5,
            proposed_axes={"availability": 0.5},
            reason_category="model_variance",
            explanation="Resolved dispute one",
            status="resolved",
            admin_note="Reviewed",
            created_at=now - timedelta(days=1),
            resolved_at=now - timedelta(days=1, hours=-10),
        ),
        McpScoreDispute(
            id=5,
            server_id=105,
            submitted_by=2,
            proposed_overall_risk=0.4,
            proposed_axes={"integrity": 0.4},
            reason_category="data_quality",
            explanation="Resolved dispute two",
            status="resolved",
            admin_note="Reviewed",
            created_at=now - timedelta(days=1),
            resolved_at=now - timedelta(hours=12),
        ),
        McpScoreDispute(
            id=6,
            server_id=106,
            submitted_by=1,
            proposed_overall_risk=0.3,
            proposed_axes={"confidentiality": 0.3},
            reason_category="model_variance",
            explanation="Resolved dispute three",
            status="resolved",
            admin_note="Reviewed",
            created_at=now - timedelta(days=1),
            resolved_at=now - timedelta(hours=12),
        ),
    ]

    test_session = Session(engine)
    try:
        test_session.add_all(rows)
        test_session.commit()
    finally:
        test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/lifecycle")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 6
    assert payload["by_status"]["resolved"] == 3
    assert payload["by_reason"]["data_quality"] == 3
    assert abs(float(payload["open_age_days"]["median"]) - 2.0) < 0.01
    assert abs(float(payload["resolution_latency_hours"]["median"]) - 12.0) < 0.01
    print("PASS")


if __name__ == "__main__":
    _run_self_test()