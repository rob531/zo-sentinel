from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpScoreDispute


class DisputeOutcomeResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason_category: Dict[str, int]
    with_admin_note: int
    without_admin_note: int
    median_resolution_hours: Optional[float]
    resolved_count: int
    open_count: int


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def get_dispute_outcomes(
    db,
    status: Optional[str] = None,
    days: Optional[int] = None,
) -> Dict[str, object]:
    disputes = db.query(McpScoreDispute).all()

    if status is not None:
        disputes = [dispute for dispute in disputes if dispute.status == status]

    if days is not None:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        disputes = [
            dispute
            for dispute in disputes
            if dispute.created_at is not None
            and _naive_utc(dispute.created_at) >= cutoff
        ]

    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    resolution_hours = []
    with_admin_note = 0

    for dispute in disputes:
        dispute_status = dispute.status or ""
        by_status[dispute_status] = by_status.get(dispute_status, 0) + 1

        reason_category = dispute.reason_category or ""
        by_reason_category[reason_category] = (
            by_reason_category.get(reason_category, 0) + 1
        )

        if dispute.admin_note is not None and str(dispute.admin_note).strip():
            with_admin_note += 1

        if dispute.created_at is not None and dispute.resolved_at is not None:
            delta = dispute.resolved_at - dispute.created_at
            resolution_hours.append(delta.total_seconds() / 3600.0)

    resolved_count = len(resolution_hours)
    median_resolution_hours = None
    if resolution_hours:
        ordered = sorted(resolution_hours)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            median_resolution_hours = float(ordered[midpoint])
        else:
            median_resolution_hours = float(
                (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
            )

    total = len(disputes)
    return {
        "total": total,
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "with_admin_note": with_admin_note,
        "without_admin_note": total - with_admin_note,
        "median_resolution_hours": median_resolution_hours,
        "resolved_count": resolved_count,
        "open_count": total - resolved_count,
    }


get_dispute_resolution_outcome_mix = get_dispute_outcomes
get_dispute_resolution_outcomes = get_dispute_outcomes
get_dispute_outcome_mix = get_dispute_outcomes
get_outcomes = get_dispute_outcomes


if __name__ == "__main__":
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
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        {
            "id": 1,
            "server_id": "server-1",
            "submitted_by": "user-1",
            "proposed_overall_risk": "high",
            "proposed_axes": "{}",
            "reason_category": "model_error",
            "explanation": "First dispute",
            "status": "resolved",
            "admin_note": "Accepted",
            "created_at": now - timedelta(hours=5),
            "resolved_at": now - timedelta(hours=4),
        },
        {
            "id": 2,
            "server_id": "server-2",
            "submitted_by": "user-2",
            "proposed_overall_risk": "medium",
            "proposed_axes": "{}",
            "reason_category": "context",
            "explanation": "Second dispute",
            "status": "resolved",
            "admin_note": "Reviewed",
            "created_at": now - timedelta(hours=4),
            "resolved_at": now - timedelta(hours=2),
        },
        {
            "id": 3,
            "server_id": "server-3",
            "submitted_by": "user-3",
            "proposed_overall_risk": "low",
            "proposed_axes": "{}",
            "reason_category": "model_error",
            "explanation": "Third dispute",
            "status": "resolved",
            "admin_note": None,
            "created_at": now - timedelta(hours=3),
            "resolved_at": now,
        },
        {
            "id": 4,
            "server_id": "server-4",
            "submitted_by": "user-4",
            "proposed_overall_risk": "medium",
            "proposed_axes": "{}",
            "reason_category": "policy",
            "explanation": "Fourth dispute",
            "status": "pending",
            "admin_note": None,
            "created_at": now - timedelta(hours=1),
            "resolved_at": None,
        },
    ]

    with SessionLocal() as session:
        session.execute(McpScoreDispute.__table__.insert(), rows)
        session.commit()

    test_app = FastAPI()

    @test_app.get(
        "/api/disputes/outcomes",
        response_model=DisputeOutcomeResponse,
    )
    def dispute_outcomes(
        db=Depends(get_session),
        status: Optional[str] = None,
        days: Optional[int] = Query(default=None, ge=0),
    ):
        return get_dispute_outcomes(db, status=status, days=days)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/outcomes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["resolved_count"] == 3
    assert payload["open_count"] == 1
    assert isinstance(payload["median_resolution_hours"], float)
    assert payload["median_resolution_hours"] >= 0

    print("PASS")