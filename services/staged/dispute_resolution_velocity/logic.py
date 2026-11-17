from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


OPEN_STATUSES = ("resolved", "rejected")


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolution_hours(created_at: Any, resolved_at: Any) -> float:
    if not isinstance(created_at, (datetime, date)) or not isinstance(
        resolved_at, (datetime, date)
    ):
        return 0.0

    created = datetime.combine(created_at, datetime.min.time()) if isinstance(
        created_at, date
    ) and not isinstance(created_at, datetime) else created_at
    resolved = datetime.combine(resolved, datetime.min.time()) if isinstance(
        resolved_at, date
    ) and not isinstance(resolved_at, datetime) else resolved_at

    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    if resolved.tzinfo is not None:
        resolved = resolved.astimezone(timezone.utc).replace(tzinfo=None)

    return max(0.0, (resolved - created).total_seconds() / 3600.0)


def compute_dispute_resolution_velocity(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compute dispute velocity and resolution metrics for the requested window."""
    cutoff = _utc_naive_now() - timedelta(days=max(0, int(days)))

    submitted_rows = list(
        db.execute(
            select(McpScoreDispute).where(McpScoreDispute.created_at >= cutoff)
        ).scalars()
    )
    resolved_rows = list(
        db.execute(
            select(McpScoreDispute).where(
                McpScoreDispute.resolved_at.is_not(None),
                McpScoreDispute.resolved_at >= cutoff,
            )
        ).scalars()
    )
    open_rows = list(
        db.execute(
            select(McpScoreDispute).where(
                McpScoreDispute.status.notin_(OPEN_STATUSES)
            )
        ).scalars()
    )

    submitted_by_reason: Dict[str, int] = {}
    for row in submitted_rows:
        category = row.reason_category
        submitted_by_reason[category] = submitted_by_reason.get(category, 0) + 1

    resolved_by_reason: Dict[str, int] = {}
    latencies: list[float] = []
    for row in resolved_rows:
        category = row.reason_category
        resolved_by_reason[category] = resolved_by_reason.get(category, 0) + 1
        latencies.append(_resolution_hours(row.created_at, row.resolved_at))

    open_by_reason: Dict[str, int] = {}
    for row in open_rows:
        category = row.reason_category
        open_by_reason[category] = open_by_reason.get(category, 0) + 1

    categories = sorted(
        set(submitted_by_reason)
        | set(resolved_by_reason)
        | set(open_by_reason),
        key=lambda value: (value is None, str(value)),
    )
    by_reason = [
        {
            "reason_category": category,
            "submitted": submitted_by_reason.get(category, 0),
            "resolved": resolved_by_reason.get(category, 0),
            "open": open_by_reason.get(category, 0),
        }
        for category in categories
    ]

    return {
        "days": int(days),
        "submitted": len(submitted_rows),
        "resolved": len(resolved_rows),
        "open": len(open_rows),
        "median_resolution_hours": (
            float(median(latencies)) if latencies else None
        ),
        "by_reason": by_reason,
    }


get_dispute_resolution_velocity = compute_dispute_resolution_velocity
get_dispute_velocity = compute_dispute_resolution_velocity
get_velocity_metrics = compute_dispute_resolution_velocity


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = _utc_naive_now()
    with SessionLocal() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id=1,
                    submitted_by=1,
                    proposed_overall_risk=0.5,
                    reason_category="scoring",
                    status="resolved",
                    created_at=now - timedelta(hours=3),
                    resolved_at=now - timedelta(hours=1),
                    explanation="test dispute",
                ),
                McpScoreDispute(
                    id=2,
                    server_id=1,
                    submitted_by=1,
                    proposed_overall_risk=0.5,
                    reason_category="scoring",
                    status="resolved",
                    created_at=now - timedelta(hours=2),
                    resolved_at=now,
                    explanation="test dispute",
                ),
                McpScoreDispute(
                    id=3,
                    server_id=1,
                    submitted_by=1,
                    proposed_overall_risk=0.5,
                    reason_category="scoring",
                    status="pending",
                    created_at=now - timedelta(hours=1),
                    resolved_at=None,
                    explanation="test dispute",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/velocity")
    def dispute_velocity(days: int = 30, db: Session = Depends(get_session)):
        result = compute_dispute_resolution_velocity(db, days)
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        return result

    response = TestClient(test_app).get("/api/disputes/velocity?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["submitted"] == 3
    assert payload["resolved"] == 2
    assert payload["open"] == 1
    assert abs(payload["median_resolution_hours"] - 2.0) <= 1e-6
    assert len(payload["by_reason"]) == 1
    print("PASS")