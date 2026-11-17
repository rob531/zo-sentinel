"""Logic layer for dispute resolution metrics."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpScoreDispute


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: List[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def get_resolution_metrics(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compute dispute counts, resolution latency, and daily activity.

    The query only uses SQLAlchemy expressions and the authoritative
    ``McpScoreDispute`` model, making it portable across supported databases.
    """
    if days < 1:
        raise ValueError("days must be at least 1")

    rows = db.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
    ).all()

    by_status: Dict[str, int] = {}
    by_reason_counts: Dict[str, int] = {}
    resolution_hours: List[float] = []
    resolved_count = 0

    for status, reason_category, created_at, resolved_at in rows:
        status_key = status or "unknown"
        by_status[status_key] = by_status.get(status_key, 0) + 1

        reason_key = reason_category or "unknown"
        by_reason_counts[reason_key] = by_reason_counts.get(reason_key, 0) + 1

        created = _as_utc(created_at)
        resolved = _as_utc(resolved_at)
        if created is not None and resolved is not None:
            hours = (resolved - created).total_seconds() / 3600.0
            if hours >= 0:
                resolution_hours.append(hours)
                resolved_count += 1

    by_reason = [
        {"reason_category": reason, "count": count}
        for reason, count in sorted(
            by_reason_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)
    created_by_date: Dict[date, int] = {}
    resolved_by_date: Dict[date, int] = {}

    for _, _, created_at, resolved_at in rows:
        created = _as_utc(created_at)
        resolved = _as_utc(resolved_at)
        if created is not None and start_date <= created.date() <= today:
            created_by_date[created.date()] = (
                created_by_date.get(created.date(), 0) + 1
            )
        if resolved is not None and start_date <= resolved.date() <= today:
            resolved_by_date[resolved.date()] = (
                resolved_by_date.get(resolved.date(), 0) + 1
            )

    daily = []
    for offset in range(days):
        bucket_date = start_date + timedelta(days=offset)
        daily.append(
            {
                "date": bucket_date.isoformat(),
                "created": created_by_date.get(bucket_date, 0),
                "resolved": resolved_by_date.get(bucket_date, 0),
            }
        )

    median_hours = float(median(resolution_hours)) if resolution_hours else None
    p90_hours = _percentile(resolution_hours, 0.90)

    return {
        "total": len(rows),
        "by_status": by_status,
        "by_reason": by_reason,
        "resolution_hours": {
            "median": median_hours,
            "p90": p90_hours,
            "resolved_count": resolved_count,
        },
        "daily": daily,
    }


# Keep common naming variants available to thin routers without duplicating
# the implementation.
get_dispute_resolution_metrics = get_resolution_metrics
compute_resolution_metrics = get_resolution_metrics
resolution_metrics = get_resolution_metrics


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    local_app = FastAPI()

    @local_app.get("/api/disputes/resolution-metrics")
    def local_resolution_metrics(
        days: int = 30,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_resolution_metrics(db, days=days)

    local_app.dependency_overrides[get_session] = lambda: SessionLocal()

    with SessionLocal() as session:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        session.add_all(
            [
                McpScoreDispute(
                    server_id="server-1",
                    submitted_by="user-1",
                    proposed_overall_risk=0.8,
                    proposed_axes={"risk": 0.8},
                    explanation="Known two-hour resolution.",
                    reason_category="axis_mismatch",
                    status="resolved",
                    created_at=now - timedelta(hours=2),
                    resolved_at=now,
                ),
                McpScoreDispute(
                    server_id="server-2",
                    submitted_by="user-2",
                    proposed_overall_risk=0.6,
                    proposed_axes={"risk": 0.6},
                    explanation="Known twenty-six-hour resolution.",
                    reason_category="axis_mismatch",
                    status="resolved",
                    created_at=now - timedelta(hours=26),
                    resolved_at=now,
                ),
                McpScoreDispute(
                    server_id="server-3",
                    submitted_by="user-3",
                    proposed_overall_risk=0.4,
                    proposed_axes={"risk": 0.4},
                    explanation="Open dispute.",
                    reason_category="model_disagreement",
                    status="open",
                    created_at=now,
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id="server-4",
                    submitted_by="user-4",
                    proposed_overall_risk=0.3,
                    proposed_axes={"risk": 0.3},
                    explanation="Open dispute.",
                    reason_category="model_disagreement",
                    status="open",
                    created_at=now,
                    resolved_at=None,
                ),
            ]
        )
        session.commit()

    client = TestClient(local_app)
    response = client.get("/api/disputes/resolution-metrics", params={"days": 30})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["resolution_hours"]["resolved_count"] == 2
    assert payload["resolution_hours"]["median"] == 14.0
    assert len(payload["daily"]) == 30
    assert sum(bucket["created"] for bucket in payload["daily"]) == 4
    assert sum(bucket["resolved"] for bucket in payload["daily"]) == 2
    print("PASS")