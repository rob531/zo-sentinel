from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey

router = APIRouter()

_BUCKETS = (
    "0-7",
    "8-30",
    "31-90",
    "91-180",
    "181-365",
    "365+",
)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    if age_days <= 365:
        return "181-365"
    return "365+"


def _coerce_org_id(org_id: str):
    if ApiKey.__table__.c.org_id.type.python_type is UUID:
        return UUID(org_id)
    return org_id


def _key_age_days(created_at: datetime, now: datetime) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, int((now - created_at).total_seconds() // 86400))


@router.get("/keys/age-distribution")
def get_api_key_age_distribution(
    db: Session = Depends(get_session),
    org_id: str = Query(...),
) -> dict:
    rows = db.execute(
        select(ApiKey.created_at, ApiKey.label).where(
            ApiKey.org_id == _coerce_org_id(org_id)
        )
    ).all()

    counts = {bucket: 0 for bucket in _BUCKETS}
    unlabelled_keys = 0
    ages = []
    now = datetime.now(timezone.utc)

    for created_at, label in rows:
        age_days = _key_age_days(created_at, now)
        ages.append(age_days)
        counts[_bucket_for_age(age_days)] += 1
        if label is None or not label.strip():
            unlabelled_keys += 1

    return {
        "org_id": org_id,
        "total_keys": len(rows),
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in _BUCKETS
        ],
        "unlabelled_keys": unlabelled_keys,
        "oldest_key_age_days": max(ages) if ages else None,
        "newest_key_age_days": min(ages) if ages else None,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
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

    test_org_id = "org-api-key-age-distribution"
    stored_org_id = _coerce_org_id(test_org_id)
    now = datetime.now(timezone.utc)
    offsets = (2, 20, 60, 120, 400)

    for index, offset in enumerate(offsets, start=1):
        id_column = ApiKey.__table__.c.id
        if id_column.type.python_type is UUID:
            key_id = uuid4()
        elif isinstance(id_column.type.python_type, int):
            key_id = index
        else:
            key_id = f"api-key-{index}"

        with TestSession() as session:
            session.add(
                ApiKey(
                    id=key_id,
                    org_id=stored_org_id,
                    key_hash=f"test-key-hash-{index}",
                    label="" if index == 1 else f"key-{index}",
                    created_at=now - timedelta(days=offset),
                )
            )
            session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/keys/age-distribution",
        params={"org_id": test_org_id},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_keys"] == 5
    assert sum(item["count"] for item in payload["buckets"]) == payload["total_keys"]
    assert payload["unlabelled_keys"] == 1
    assert payload["oldest_key_age_days"] == 400
    assert payload["newest_key_age_days"] == 2

    print("PASS")