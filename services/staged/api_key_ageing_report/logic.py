from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey


AGE_BUCKETS = ("0-30", "31-90", "91-180", "181+")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime) -> int:
    return max(0, (now - _as_utc(created_at)).days)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 30:
        return "0-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    return "181+"


def get_api_key_ageing_report(db: Session, days: int) -> Dict[str, Any]:
    """Return API-key age buckets and keys older than the requested threshold."""
    threshold_days = int(days)
    if threshold_days < 0:
        raise ValueError("days must be non-negative")

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(ApiKey.id, ApiKey.org_id, ApiKey.label, ApiKey.created_at)
    ).all()

    counts = {bucket: 0 for bucket in AGE_BUCKETS}
    stale_keys: List[Dict[str, Any]] = []

    for key_id, org_id, label, created_at in rows:
        age = _age_days(created_at, now)
        counts[_bucket_for_age(age)] += 1
        if age > threshold_days:
            stale_keys.append(
                {
                    "id": str(key_id),
                    "org_id": str(org_id),
                    "label": str(label),
                    "age_days": age,
                    "created_at": created_at.isoformat(),
                }
            )

    stale_keys.sort(key=lambda item: (-item["age_days"], item["id"]))
    return {
        "threshold_days": threshold_days,
        "total_keys": len(rows),
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in AGE_BUCKETS
        ],
        "stale_keys": stale_keys,
    }


def api_key_ageing_report(db: Session, days: int) -> Dict[str, Any]:
    return get_api_key_ageing_report(db, days)


def build_api_key_ageing_report(db: Session, days: int) -> Dict[str, Any]:
    return get_api_key_ageing_report(db, days)


def get_key_ageing_report(db: Session, days: int) -> Dict[str, Any]:
    return get_api_key_ageing_report(db, days)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI, Query
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    test_session = TestSession()
    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            ApiKey(
                id="key-5",
                org_id="org-1",
                key_hash="hash-5",
                label="five-day-key",
                created_at=now - timedelta(days=5),
            ),
            ApiKey(
                id="key-45",
                org_id="org-1",
                key_hash="hash-45",
                label="forty-five-day-key",
                created_at=now - timedelta(days=45),
            ),
            ApiKey(
                id="key-200",
                org_id="org-1",
                key_hash="hash-200",
                label="two-hundred-day-key",
                created_at=now - timedelta(days=200),
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()

    @test_app.get("/api/keys/ageing")
    def ageing(
        days: int = Query(default=30),
        db: Session = Depends(get_session),
    ):
        return get_api_key_ageing_report(db, days)

    test_app.dependency_overrides[get_session] = override_get_session
    route = next(
        route
        for route in test_app.routes
        if getattr(route, "path", None) == "/api/keys/ageing"
    )
    result = route.endpoint(days=30, db=test_session)
    assert result["total_keys"] == 3
    assert result["buckets"][0] == {"bucket": "0-30", "count": 1}
    assert any(item["id"] == "key-200" for item in result["stale_keys"])
    assert not any(item["id"] == "key-5" for item in result["stale_keys"])
    print("PASS")