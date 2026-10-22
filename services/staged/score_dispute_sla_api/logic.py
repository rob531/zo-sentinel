from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil, floor
from statistics import median
from typing import Dict, Optional

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


STATUSES = ("open", "under_review", "approved", "rejected", "escalated")
OVERDUE_AFTER = timedelta(hours=72)


class DisputeSlaResponse(BaseModel):
    days: int
    by_status: Dict[str, int]
    by_reason: Dict[str, int]
    median_resolution_hours: Optional[float]
    p90_resolution_hours: Optional[float]
    overdue_open: int


DisputeSLASummary = DisputeSlaResponse
SLASummary = DisputeSlaResponse


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None

    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = floor(position)
    upper = ceil(position)

    if lower == upper:
        return ordered[lower]

    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def get_dispute_sla_summary(db: Session, days: int = 30) -> Dict[str, object]:
    if days < 1:
        raise ValueError("days must be at least 1")

    disputes = (
        db.query(McpScoreDispute)
        .order_by(McpScoreDispute.id)
        .all()
    )

    by_status = {status: 0 for status in STATUSES}
    by_reason: Dict[str, int] = {}
    resolution_hours = []
    overdue_open = 0
    now = datetime.now(timezone.utc)

    for dispute in disputes:
        status = str(dispute.status or "").strip().lower()
        if status in by_status:
            by_status[status] += 1

        reason = str(dispute.reason_category or "unknown").strip().lower() or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1

        if dispute.resolved_at is not None and dispute.created_at is not None:
            created_at = _utc_datetime(dispute.created_at)
            resolved_at = _utc_datetime(dispute.resolved_at)
            latency = (resolved_at - created_at).total_seconds() / 3600.0
            if latency >= 0:
                resolution_hours.append(latency)

        if (
            status == "open"
            and dispute.created_at is not None
            and dispute.resolved_at is None
            and _utc_datetime(dispute.created_at) < now - OVERDUE_AFTER
        ):
            overdue_open += 1

    median_resolution_hours = (
        float(median(resolution_hours)) if resolution_hours else None
    )
    p90_resolution_hours = _percentile(resolution_hours, 0.90)

    return {
        "days": days,
        "by_status": by_status,
        "by_reason": by_reason,
        "median_resolution_hours": median_resolution_hours,
        "p90_resolution_hours": p90_resolution_hours,
        "overdue_open": overdue_open,
    }


def summarize_disputes(db: Session, days: int = 30) -> Dict[str, object]:
    return get_dispute_sla_summary(db, days)


def get_dispute_sla(db: Session, days: int = 30) -> Dict[str, object]:
    return get_dispute_sla_summary(db, days)


def get_dispute_sla_stats(db: Session, days: int = 30) -> Dict[str, object]:
    return get_dispute_sla_summary(db, days)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=50,
            proposed_axes={},
            reason_category="scoring_error",
            explanation="First resolved dispute",
            status="approved",
            admin_note="Reviewed",
            created_at=now - timedelta(hours=4),
            resolved_at=now - timedelta(hours=3),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=60,
            proposed_axes={},
            reason_category="missing_context",
            explanation="Second resolved dispute",
            status="rejected",
            admin_note="Reviewed",
            created_at=now - timedelta(hours=6),
            resolved_at=now - timedelta(hours=4),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=70,
            proposed_axes={},
            reason_category="scoring_error",
            explanation="Slow resolved dispute",
            status="escalated",
            admin_note="Reviewed",
            created_at=now - timedelta(hours=110),
            resolved_at=now - timedelta(hours=10),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=40,
            proposed_axes={},
            reason_category="missing_context",
            explanation="Old open dispute",
            status="open",
            admin_note=None,
            created_at=now - timedelta(hours=100),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=45,
            proposed_axes={},
            reason_category="policy_disagreement",
            explanation="Second old open dispute",
            status="open",
            admin_note=None,
            created_at=now - timedelta(hours=80),
            resolved_at=None,
        ),
    ]

    session = TestSession()
    session.add_all(rows)
    session.commit()

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/disputes/sla", response_model=DisputeSlaResponse)
    def dispute_sla(
        days: int = Query(default=30, ge=1),
        db: Session = Depends(get_session),
    ):
        return get_dispute_sla_summary(db, days)

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/sla", params={"days": 30})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["by_status"] == {
        "open": 2,
        "under_review": 0,
        "approved": 1,
        "rejected": 1,
        "escalated": 1,
    }
    assert body["overdue_open"] == 2
    assert body["median_resolution_hours"] > 0
    assert body["p90_resolution_hours"] > 0

    session.close()
    test_engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()