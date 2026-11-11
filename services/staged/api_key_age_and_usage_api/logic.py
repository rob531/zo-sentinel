from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


BUCKETS = ("0-7", "8-30", "31-90", "91-365", "365+")


def _as_utc(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)


def _age_in_days(created_at: datetime | date | None, now: datetime) -> int:
    if created_at is None:
        return 0
    age = now - _as_utc(created_at)
    if age < timedelta(0):
        return 0
    return age.days


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 365:
        return "91-365"
    return "365+"


def get_key_age_profile(db: Session = Depends(get_session)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    key_rows = db.execute(
        select(ApiKey.org_id, ApiKey.created_at)
    ).all()
    org_rows = db.execute(
        select(Org.id, Org.name).order_by(Org.id)
    ).all()

    bucket_counts = Counter({bucket: 0 for bucket in BUCKETS})
    org_key_counts: Counter[Any] = Counter()
    distinct_org_ids: set[Any] = set()

    for org_id, created_at in key_rows:
        bucket_counts[_bucket_for_age(_age_in_days(created_at, now))] += 1
        if org_id is not None:
            distinct_org_ids.add(org_id)
            org_key_counts[org_id] += 1

    per_org = [
        {
            "org_id": org_id,
            "org_name": org_name,
            "key_count": org_key_counts.get(org_id, 0),
        }
        for org_id, org_name in org_rows
    ]

    return {
        "generated_at": now.isoformat(),
        "total_keys": len(key_rows),
        "distinct_orgs": len(distinct_org_ids),
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in BUCKETS
        ],
        "per_org": per_org,
    }


get_api_key_age_profile = get_key_age_profile
get_age_profile = get_key_age_profile


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    Base.metadata.create_all(
        test_engine,
        tables=[Org.__table__, ApiKey.__table__],
    )

    with TestSession() as session:
        session.add_all(
            [
                Org(id=1, name="Example Org One", created_at=datetime.now(timezone.utc)),
                Org(id=2, name="Example Org Two", created_at=datetime.now(timezone.utc)),
            ]
        )
        session.commit()

        now = datetime.now(timezone.utc)
        session.add_all(
            [
                ApiKey(
                    id=1,
                    org_id=1,
                    key_hash="test-key-hash-1",
                    label="Test Key One",
                    created_at=now - timedelta(days=2),
                ),
                ApiKey(
                    id=2,
                    org_id=1,
                    key_hash="test-key-hash-2",
                    label="Test Key Two",
                    created_at=now - timedelta(days=20),
                ),
                ApiKey(
                    id=3,
                    org_id=2,
                    key_hash="test-key-hash-3",
                    label="Test Key Three",
                    created_at=now - timedelta(days=400),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/keys/age-profile")
    def age_profile(db: Session = Depends(get_session)):
        return get_key_age_profile(db)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/keys/age-profile")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 3
        assert payload["distinct_orgs"] == 2
        assert sum(item["count"] for item in payload["buckets"]) == payload["total_keys"]

    print("PASS")