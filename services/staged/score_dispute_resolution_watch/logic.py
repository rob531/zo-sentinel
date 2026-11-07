"""Resolution-watch data and computation layer for real application disputes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


STATUS_ORDER = ("open", "under_review", "approved", "rejected", "escalated")
UNRESOLVED_STATUSES = ("open", "under_review", "escalated")


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _elapsed_hours(created_at: datetime, resolved_at: datetime) -> float:
    if created_at.tzinfo is not None and resolved_at.tzinfo is None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    elif resolved_at.tzinfo is not None and created_at.tzinfo is None:
        resolved_at = resolved_at.astimezone(timezone.utc).replace(tzinfo=None)
    elif created_at.tzinfo is not None and resolved_at.tzinfo is not None:
        created_at = created_at.astimezone(timezone.utc)
        resolved_at = resolved_at.astimezone(timezone.utc)
    return (resolved_at - created_at).total_seconds() / 3600.0


def get_resolution_watch(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return dispute status counts, resolution timing, and overdue work."""
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = _utc_naive_now() - timedelta(days=days)

    count_rows = db.execute(
        select(McpScoreDispute.status, func.count(McpScoreDispute.id)).group_by(
            McpScoreDispute.status
        )
    ).all()
    status_counts = {status: 0 for status in STATUS_ORDER}
    for status, count in count_rows:
        if status in status_counts:
            status_counts[status] = int(count)

    resolution_rows = db.execute(
        select(McpScoreDispute.created_at, McpScoreDispute.resolved_at).where(
            McpScoreDispute.created_at.is_not(None),
            McpScoreDispute.resolved_at.is_not(None),
        )
    ).all()
    resolution_hours = [
        _elapsed_hours(created_at, resolved_at)
        for created_at, resolved_at in resolution_rows
    ]
    median_resolution_hours: Optional[float] = (
        float(median(resolution_hours)) if resolution_hours else None
    )

    overdue_count = int(
        db.execute(
            select(func.count(McpScoreDispute.id)).where(
                McpScoreDispute.created_at.is_not(None),
                McpScoreDispute.created_at < cutoff,
                McpScoreDispute.status.in_(UNRESOLVED_STATUSES),
            )
        ).scalar_one()
    )

    oldest_open = db.execute(
        select(McpScoreDispute.id)
        .where(
            McpScoreDispute.status == "open",
            McpScoreDispute.created_at.is_not(None),
        )
        .order_by(McpScoreDispute.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()

    return {
        "days": days,
        "status_counts": status_counts,
        "median_resolution_hours": median_resolution_hours,
        "overdue_unresolved": overdue_count,
        "oldest_open_id": oldest_open,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


compute_resolution_watch = get_resolution_watch
get_dispute_resolution_watch = get_resolution_watch
get_score_dispute_resolution_watch = get_resolution_watch
resolution_watch = get_resolution_watch


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    created_at_column = McpScoreDispute.__table__.c.created_at
    original_nullable = created_at_column.nullable
    created_at_column.nullable = True
    try:
        McpScoreDispute.__table__.create(engine)
    finally:
        created_at_column.nullable = original_nullable

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = SessionLocal()
    session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id=1,
                submitted_by="reviewer@example.test",
                proposed_overall_risk=0.8,
                reason_category="model_score",
                explanation="First dispute",
                status="approved",
                created_at=now - timedelta(days=10),
                resolved_at=now - timedelta(days=8),
            ),
            McpScoreDispute(
                id=2,
                server_id=2,
                submitted_by="reviewer@example.test",
                proposed_overall_risk=0.4,
                reason_category="model_score",
                explanation="Second dispute",
                status="rejected",
                created_at=now - timedelta(days=5),
                resolved_at=now - timedelta(days=3),
            ),
            McpScoreDispute(
                id=3,
                server_id=3,
                submitted_by="reviewer@example.test",
                proposed_overall_risk=0.6,
                reason_category="model_score",
                explanation="Overdue dispute",
                status="open",
                created_at=now - timedelta(days=40),
            ),
            McpScoreDispute(
                id=4,
                server_id=4,
                submitted_by="reviewer@example.test",
                proposed_overall_risk=0.2,
                reason_category="model_score",
                explanation="Undated dispute",
                status="open",
                created_at=None,
            ),
        ]
    )
    session.commit()
    session.close()

    local_app = FastAPI()

    def override_get_session():
        with SessionLocal() as override_session:
            yield override_session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/disputes/resolution-watch")
    def resolution_watch_endpoint(days: int = 30, db: Session = Depends(get_session)):
        return get_resolution_watch(db, days)

    with TestClient(local_app) as client:
        response = client.get("/api/disputes/resolution-watch", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert sum(payload["status_counts"].values()) == 4
        assert payload["median_resolution_hours"] == 48
        assert payload["overdue_unresolved"] == 1
        assert payload["oldest_open_id"] == 3

    print("PASS")