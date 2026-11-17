from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select

from app.db import get_session
from app.models import ApiKey, Org


BUCKETS = ("fresh_30d", "aging_90d", "stale_over_90d")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime | None = None) -> int:
    current = _as_utc(now or datetime.now(timezone.utc))
    created = _as_utc(created_at)
    return max(0, (current - created).days)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 30:
        return "fresh_30d"
    if age_days <= 90:
        return "aging_90d"
    return "stale_over_90d"


async def get_org_api_key_expiry(
    org_id: int,
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    statement = (
        select(Org, ApiKey)
        .select_from(Org)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        .where(Org.id == org_id)
        .order_by(ApiKey.id)
    )

    result = session.execute(statement)
    if inspect.isawaitable(result):
        result = await result

    rows = result.all()
    if not rows:
        raise HTTPException(status_code=404, detail="Organization not found")

    org = rows[0][0]
    now = datetime.now(timezone.utc)
    bucket_keys: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in BUCKETS}

    for _, api_key in rows:
        if api_key is None:
            continue
        age_days = _age_days(api_key.created_at, now)
        bucket = _bucket_for_age(age_days)
        bucket_keys[bucket].append(
            {
                "id": api_key.id,
                "label": api_key.label,
                "age_days": age_days,
            }
        )

    return {
        "org_id": org.id,
        "org_name": org.name,
        "total_keys": sum(len(keys) for keys in bucket_keys.values()),
        "buckets": [
            {
                "bucket": bucket,
                "count": len(bucket_keys[bucket]),
                "keys": bucket_keys[bucket],
            }
            for bucket in BUCKETS
        ],
    }


get_api_key_expiry = get_org_api_key_expiry


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                Org(id=1, name="Alpha", created_at=now),
                Org(id=2, name="Beta", created_at=now),
            ]
        )
        session.add_all(
            [
                ApiKey(
                    id=1,
                    org_id=1,
                    key_hash="hash-secret-one",
                    label="one",
                    created_at=now - timedelta(days=5),
                ),
                ApiKey(
                    id=2,
                    org_id=2,
                    key_hash="hash-secret-two",
                    label="two",
                    created_at=now - timedelta(days=60),
                ),
                ApiKey(
                    id=3,
                    org_id=1,
                    key_hash="hash-secret-three",
                    label="three",
                    created_at=now - timedelta(days=200),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/orgs/{org_id}/api-keys/expiry")
    async def api_key_expiry_endpoint(org_id: int, session: Any = Depends(get_session)):
        return await get_org_api_key_expiry(org_id, session)

    def override_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/1/api-keys/expiry")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 2
        assert {item["bucket"] for item in payload["buckets"] if item["count"]} == {
            "fresh_30d",
            "stale_over_90d",
        }
        assert "hash-secret-one" not in response.text
        assert "hash-secret-three" not in response.text

        response = client.get("/api/orgs/2/api-keys/expiry")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 1
        assert payload["buckets"][1]["bucket"] == "aging_90d"
        assert payload["buckets"][1]["count"] == 1
        assert "hash-secret-two" not in response.text

    print("PASS")