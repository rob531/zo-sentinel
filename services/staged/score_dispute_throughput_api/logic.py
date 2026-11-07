"""Read-only dispute throughput and backlog calculations over the real app models."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


router = APIRouter(prefix="/api")


class StatusThroughput(BaseModel):
    status: str
    count: int
    oldest_open_days: Optional[float]


class ReasonThroughput(BaseModel):
    reason_category: str
    count: int


class DailyThroughput(BaseModel):
    date: str
    submitted: int
    resolved: int


class DisputeThroughputResponse(BaseModel):
    days: int
    submitted: int
    resolved: int
    open_count: int
    median_resolution_hours: Optional[float]
    by_status: List[StatusThroughput]
    by_reason: List[ReasonThroughput]
    daily: List[DailyThroughput]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_open(dispute: McpScoreDispute) -> bool:
    return (dispute.status or "").strip().lower() == "open"


def get_dispute_throughput(
    db: Session,
    days: int = 30,
    status: Optional[str] = None,
    now: Optional[datetime] = None,
) -> DisputeThroughputResponse:
    """Calculate dispute throughput, resolution latency, and current backlog."""
    if days < 1:
        raise ValueError("days must be at least 1")

    current = _as_utc(now or datetime.now(timezone.utc))
    cutoff = current - timedelta(days=days)

    query = select(McpScoreDispute)
    if status is not None:
        query = query.where(McpScoreDispute.status == status)

    disputes = list(db.execute(query).scalars().all())

    submitted_rows = [
        row
        for row in disputes
        if row.created_at is not None and _as_utc(row.created_at) >= cutoff
    ]
    resolved_rows = [
        row
        for row in disputes
        if row.resolved_at is not None and _as_utc(row.resolved_at) >= cutoff
    ]

    resolution_hours = [
        (
            _as_utc(row.resolved_at) - _as_utc(row.created_at)
        ).total_seconds()
        / 3600.0
        for row in resolved_rows
        if row.created_at is not None and row.resolved_at is not None
    ]
    median_resolution_hours = (
        round(float(statistics.median(resolution_hours)), 2)
        if resolution_hours
        else None
    )

    status_groups: Dict[str, List[McpScoreDispute]] = {}
    for row in disputes:
        key = row.status or "unknown"
        status_groups.setdefault(key, []).append(row)

    by_status = []
    for key in sorted(status_groups):
        rows = status_groups[key]
        open_rows = [row for row in rows if _is_open(row)]
        oldest_open_days = None
        if open_rows:
            oldest_open_days = round(
                max(
                    0.0,
                    max(
                        (current - _as_utc(row.created_at)).total_seconds()
                        / 86400.0
                        for row in open_rows
                        if row.created_at is not None
                    ),
                ),
                2,
            )
        by_status.append(
            StatusThroughput(
                status=key,
                count=len(rows),
                oldest_open_days=oldest_open_days,
            )
        )

    reason_groups: Dict[str, int] = {}
    for row in disputes:
        reason = row.reason_category or "unknown"
        reason_groups[reason] = reason_groups.get(reason, 0) + 1

    by_reason = [
        ReasonThroughput(reason_category=reason, count=count)
        for reason, count in sorted(reason_groups.items())
    ]

    daily_counts: Dict[str, Dict[str, int]] = {}
    for offset in range(days):
        day = (cutoff + timedelta(days=offset)).date().isoformat()
        daily_counts[day] = {"submitted": 0, "resolved": 0}

    for row in submitted_rows:
        day = _as_utc(row.created_at).date().isoformat()
        if day in daily_counts:
            daily_counts[day]["submitted"] += 1

    for row in resolved_rows:
        day = _as_utc(row.resolved_at).date().isoformat()
        if day in daily_counts:
            daily_counts[day]["resolved"] += 1

    daily = [
        DailyThroughput(date=day, submitted=counts["submitted"], resolved=counts["resolved"])
        for day, counts in sorted(daily_counts.items())
    ]

    open_count = sum(1 for row in disputes if _is_open(row))

    return DisputeThroughputResponse(
        days=days,
        submitted=len(submitted_rows),
        resolved=len(resolved_rows),
        open_count=open_count,
        median_resolution_hours=median_resolution_hours,
        by_status=by_status,
        by_reason=by_reason,
        daily=daily,
    )


get_throughput = get_dispute_throughput


@router.get(
    "/disputes/throughput",
    response_model=DisputeThroughputResponse,
)
def dispute_throughput(
    days: int = Query(default=30, ge=1),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> DisputeThroughputResponse:
    return get_dispute_throughput(db=db, days=days, status=status)


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_db = TestSession()

    base = datetime.now(timezone.utc)
    seeded = [
        McpScoreDispute(
            server_id=1,
            submitted_by="self-test",
            proposed_overall_risk=10.0,
            reason_category="incorrect_score",
            explanation="self-test dispute",
            status="open",
            created_at=base - timedelta(days=5),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="self-test",
            proposed_overall_risk=20.0,
            reason_category="incorrect_score",
            explanation="self-test dispute",
            status="accepted",
            created_at=base - timedelta(days=4),
            resolved_at=base - timedelta(days=4) + timedelta(hours=2),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="self-test",
            proposed_overall_risk=30.0,
            reason_category="wrong_signal",
            explanation="self-test dispute",
            status="rejected",
            created_at=base - timedelta(days=3),
            resolved_at=base - timedelta(days=3) + timedelta(hours=4),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="self-test",
            proposed_overall_risk=40.0,
            reason_category="wrong_signal",
            explanation="self-test dispute",
            status="open",
            created_at=base - timedelta(days=2),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="self-test",
            proposed_overall_risk=50.0,
            reason_category="incorrect_score",
            explanation="self-test dispute",
            status="accepted",
            created_at=base - timedelta(days=1),
            resolved_at=base - timedelta(days=1) + timedelta(hours=1),
        ),
    ]
    test_db.add_all(seeded)
    test_db.commit()

    def override_get_session():
        yield test_db

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/disputes/throughput?days=30")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["submitted"] == 5, payload
    assert payload["open_count"] == 2, payload
    assert abs(payload["median_resolution_hours"] - 2.0) <= 0.01, payload
    dates = [entry["date"] for entry in payload["daily"]]
    assert dates == sorted(dates), payload

    test_db.close()
    test_engine.dispose()
    print("PASS")