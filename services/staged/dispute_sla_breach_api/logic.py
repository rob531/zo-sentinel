from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api")

_CLOSED_STATUSES = {"resolved", "rejected", "withdrawn"}
_AGE_BUCKETS = ("0-1d", "1-7d", "7-30d", "30d+")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_hours(created_at: datetime, now: datetime) -> float:
    return max(0.0, (now - created_at).total_seconds() / 3600.0)


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2.0)


def aggregate_dispute_sla(
    rows: Iterable[McpScoreDispute],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    reference = _as_utc(now or datetime.now(timezone.utc))
    materialized = list(rows)

    by_status: dict[str, int] = {}
    by_reason_category: dict[str, int] = {}
    open_age_buckets = {bucket: 0 for bucket in _AGE_BUCKETS}
    resolution_hours: list[float] = []
    open_disputes = 0

    for dispute in materialized:
        status = str(dispute.status)
        by_status[status] = by_status.get(status, 0) + 1

        category = str(dispute.reason_category)
        by_reason_category[category] = by_reason_category.get(category, 0) + 1

        if status not in _CLOSED_STATUSES:
            open_disputes += 1
            if dispute.created_at is not None:
                age = _age_hours(_as_utc(dispute.created_at), reference)
                if age < 24:
                    bucket = "0-1d"
                elif age < 7 * 24:
                    bucket = "1-7d"
                elif age < 30 * 24:
                    bucket = "7-30d"
                else:
                    bucket = "30d+"
                open_age_buckets[bucket] += 1

        if dispute.created_at is not None and dispute.resolved_at is not None:
            elapsed = (
                _as_utc(dispute.resolved_at) - _as_utc(dispute.created_at)
            ).total_seconds() / 3600.0
            resolution_hours.append(elapsed)

    return {
        "total": len(materialized),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "open_disputes": open_disputes,
        "open_age_buckets": [
            {"bucket": bucket, "count": open_age_buckets[bucket]}
            for bucket in _AGE_BUCKETS
        ],
        "median_hours_to_resolve": _median(resolution_hours),
    }


def fetch_dispute_sla(
    session: Session,
    status: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    statement = select(McpScoreDispute)
    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)
    return aggregate_dispute_sla(session.scalars(statement).all(), now=now)


@router.get("/disputes/sla")
def get_dispute_sla(
    status: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return fetch_dispute_sla(session, status=status)


def _self_test() -> None:
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
    McpScoreDispute.__table__.create(engine)
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    seed_rows = [
        McpScoreDispute(
            server_id="server-1",
            submitted_by="analyst-1",
            proposed_overall_risk=50.0,
            reason_category="seeded-category",
            explanation="Two-hour resolution",
            status="resolved",
            created_at=now - timedelta(hours=32),
            resolved_at=now - timedelta(hours=30),
        ),
        McpScoreDispute(
            server_id="server-2",
            submitted_by="analyst-1",
            proposed_overall_risk=60.0,
            reason_category="seeded-category",
            explanation="Thirty-hour resolution",
            status="resolved",
            created_at=now - timedelta(hours=30),
            resolved_at=now,
        ),
        McpScoreDispute(
            server_id="server-3",
            submitted_by="analyst-2",
            proposed_overall_risk=70.0,
            reason_category="other-category",
            explanation="Forty-day-old open dispute",
            status="open",
            created_at=now - timedelta(days=40),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id="server-4",
            submitted_by="analyst-2",
            proposed_overall_risk=40.0,
            reason_category="other-category",
            explanation="Two-day-old open dispute",
            status="in_review",
            created_at=now - timedelta(days=2),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id="server-5",
            submitted_by="analyst-3",
            proposed_overall_risk=30.0,
            reason_category="other-category",
            explanation="Pending dispute",
            status="pending",
            created_at=now,
            resolved_at=None,
        ),
    ]

    with SessionFactory() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with SessionFactory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/sla")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 5
    assert payload["open_disputes"] == 3
    buckets = {item["bucket"]: item["count"] for item in payload["open_age_buckets"]}
    assert buckets["30d+"] == 1
    assert payload["by_reason_category"]["seeded-category"] == 2
    assert payload["median_hours_to_resolve"] == 16.0
    print("PASS")


if __name__ == "__main__":
    _self_test()