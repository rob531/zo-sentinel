"""logic.py -- dispute ageing bucket data/computation layer."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_BUCKETS = ("0-7", "8-30", "31-90", "90+")


def _as_utc(value: object) -> Optional[datetime]:
    """Convert a database date/time value to an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def dispute_ageing_buckets(db: Session) -> Dict[str, object]:
    """Return OPEN dispute ageing buckets and dispute status counts."""
    rows = db.execute(
        select(
            McpScoreDispute.id,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            func.now().label("db_now"),
        )
    ).all()

    by_status: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {bucket: 0 for bucket in _BUCKETS}
    oldest_created_at: Optional[datetime] = None
    oldest_open_dispute_id: object = None
    total_open = 0

    for row in rows:
        status = row.status
        status_key = str(status) if status is not None else "unknown"
        by_status[status_key] = by_status.get(status_key, 0) + 1

        if str(status).upper() != "OPEN":
            continue

        total_open += 1
        created_at = _as_utc(row.created_at)
        db_now = _as_utc(row.db_now)
        if created_at is None:
            age_days = 0
        elif db_now is None:
            age_days = 0
        else:
            age_days = max(0, int((db_now - created_at).total_seconds() // 86400))

        bucket_counts[_bucket_for_age(age_days)] += 1

        if created_at is not None and (
            oldest_created_at is None or created_at < oldest_created_at
        ):
            oldest_created_at = created_at
            oldest_open_dispute_id = row.id

    buckets: List[Dict[str, object]] = [
        {"bucket": bucket, "count": bucket_counts[bucket]} for bucket in _BUCKETS
    ]

    return {
        "buckets": buckets,
        "by_status": by_status,
        "oldest_open_dispute_id": oldest_open_dispute_id,
        "total_open": total_open,
    }


get_dispute_ageing_buckets = dispute_ageing_buckets
get_ageing_buckets = dispute_ageing_buckets
compute_dispute_ageing_buckets = dispute_ageing_buckets


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
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
    SessionFactory = sessionmaker(bind=test_engine)
    test_db = SessionFactory()

    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    test_db.add_all(
        [
            McpScoreDispute(
                server_id=1,
                status="OPEN",
                reason_category="test",
                proposed_overall_risk=0.5,
                explanation="open dispute",
                submitted_by="contract",
                created_at=base_time - timedelta(days=2),
            ),
            McpScoreDispute(
                server_id=1,
                status="OPEN",
                reason_category="test",
                proposed_overall_risk=0.5,
                explanation="older open dispute",
                submitted_by="contract",
                created_at=base_time - timedelta(days=40),
            ),
            McpScoreDispute(
                server_id=1,
                status="RESOLVED",
                reason_category="test",
                proposed_overall_risk=0.5,
                explanation="resolved dispute",
                submitted_by="contract",
                created_at=base_time - timedelta(days=10),
                resolved_at=base_time - timedelta(days=1),
            ),
        ]
    )
    test_db.commit()

    that_app = FastAPI()

    @that_app.get("/api/disputes/ageing-buckets")
    def ageing_buckets_endpoint(db: Session = Depends(get_session)):
        return dispute_ageing_buckets(db)

    def override_get_session():
        yield test_db

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/ageing-buckets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_open"] == 2
        assert next(
            bucket["count"]
            for bucket in payload["buckets"]
            if bucket["bucket"] == "31-90"
        ) == 1

    print("PASS")