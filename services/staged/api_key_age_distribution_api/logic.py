from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey


BUCKETS = (
    ("0-7", 0, 7),
    ("8-30", 8, 30),
    ("31-90", 31, 90),
    ("91-365", 91, 364),
    ("365+", 365, None),
)


def _age_in_days(created_at: datetime, now: datetime) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    return max(0, int((now - created_at).total_seconds() // 86400))


def _bucket_for_age(age_days: int) -> str:
    for bucket, lower, upper in BUCKETS:
        if age_days >= lower and (upper is None or age_days <= upper):
            return bucket
    return BUCKETS[-1][0]


def calculate_age_distribution(
    session: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference_time = now or datetime.now(timezone.utc)
    rows = session.query(ApiKey.created_at, ApiKey.org_id).all()

    counts = {bucket: 0 for bucket, _, _ in BUCKETS}
    org_ids: set[Any] = set()
    stale_over_90d = 0

    for created_at, org_id in rows:
        if org_id is not None:
            org_ids.add(org_id)

        age_days = _age_in_days(created_at, reference_time)
        counts[_bucket_for_age(age_days)] += 1
        if age_days > 90:
            stale_over_90d += 1

    return {
        "total_keys": len(rows),
        "distinct_orgs": len(org_ids),
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket, _, _ in BUCKETS
        ],
        "stale_over_90d": stale_over_90d,
    }


def get_age_distribution(
    session: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    return calculate_age_distribution(session, now=now)


def get_api_key_age_distribution(
    session: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    return calculate_age_distribution(session, now=now)


def compute_age_distribution(
    session: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    return calculate_age_distribution(session, now=now)


if __name__ == "__main__":
    import uuid
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    def test_identifier(column: Any, ordinal: int) -> Any:
        try:
            python_type = column.type.python_type
        except NotImplementedError:
            python_type = str

        if python_type is int:
            return ordinal
        if python_type is uuid.UUID:
            return uuid.UUID(int=ordinal)
        return f"{ordinal:032x}"

    with TestSession() as session:
        for ordinal, offset in enumerate((1, 10, 45, 120, 400), start=1):
            session.add(
                ApiKey(
                    id=test_identifier(ApiKey.__table__.c.id, ordinal),
                    org_id=test_identifier(
                        ApiKey.__table__.c.org_id,
                        ordinal if ordinal <= 4 else 4,
                    ),
                    key_hash=f"hash-{ordinal}",
                    label=f"Key {ordinal}",
                    created_at=datetime.now(timezone.utc)
                    - timedelta(days=offset),
                )
            )
        session.commit()

    def override_get_session() -> Iterable[Session]:
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/api-keys/age-distribution")
    def age_distribution(session: Session = Depends(get_session)) -> dict[str, Any]:
        return calculate_age_distribution(session)

    with TestClient(that_app) as client:
        response = client.get("/api/api-keys/age-distribution")
        payload = response.json()

        assert response.status_code == 200
        assert payload["total_keys"] == 5
        assert sum(item["count"] for item in payload["buckets"]) == 5
        assert payload["stale_over_90d"] == 2
        assert payload["distinct_orgs"] == 4
        assert [item["count"] for item in payload["buckets"]] == [1, 1, 1, 1, 1]

    print("PASS")