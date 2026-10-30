from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: datetime, now: datetime) -> int:
    return max(0, int((now - _utc_datetime(value)).total_seconds() // 86400))


def get_org_api_key_usage_rollup(
    org_id: str,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if isinstance(org_id, Session) and isinstance(db, str):
        org_id, db = db, org_id

    org = db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    api_keys = list(
        db.execute(
            select(ApiKey)
            .where(ApiKey.org_id == org_id)
            .order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
        ).scalars()
    )
    member_count = int(
        db.execute(
            select(func.count(User.id)).where(User.org_id == org_id)
        ).scalar_one()
    )

    now = datetime.now(timezone.utc)
    ages = [_age_days(api_key.created_at, now) for api_key in api_keys]

    return {
        "org_id": org.id,
        "org_name": org.name,
        "total_keys": len(api_keys),
        "labels": sorted(
            {api_key.label for api_key in api_keys if api_key.label is not None}
        ),
        "oldest_key_age_days": max(ages, default=0),
        "newest_key_age_days": min(ages, default=0),
        "member_count": member_count,
        "keys": [
            {
                "id": api_key.id,
                "label": api_key.label,
                "created_at": api_key.created_at,
                "masked_hash": (api_key.key_hash or "")[:6],
            }
            for api_key in api_keys
        ],
    }


get_org_api_key_rollup = get_org_api_key_usage_rollup
get_api_key_usage_rollup = get_org_api_key_usage_rollup
get_api_key_rollup = get_org_api_key_usage_rollup


def _self_test() -> None:
    from datetime import timedelta

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
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)
    User.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        db.add(Org(id="org-rollup-test", name="Rollup Test Org", created_at=now))
        db.add_all(
            [
                ApiKey(
                    id="key-rollup-1",
                    org_id="org-rollup-test",
                    key_hash="secret-hash-one",
                    label="primary",
                    created_at=now - timedelta(days=30),
                ),
                ApiKey(
                    id="key-rollup-2",
                    org_id="org-rollup-test",
                    key_hash="secret-hash-two",
                    label="secondary",
                    created_at=now - timedelta(days=2),
                ),
                User(
                    id="user-rollup-1",
                    org_id="org-rollup-test",
                    role="admin",
                    created_at=now,
                    email="admin@example.com",
                    password_hash="not-returned",
                ),
                User(
                    id="user-rollup-2",
                    org_id="org-rollup-test",
                    role="member",
                    created_at=now,
                    email="member@example.com",
                    password_hash="not-returned",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestingSession() as db:
            yield db

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/orgs/{org_id}/api-key-rollup")
    def api_key_rollup(org_id: str, db: Session = Depends(get_session)):
        return get_org_api_key_usage_rollup(org_id, db)

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/org-rollup-test/api-key-rollup")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 2
        assert payload["member_count"] == 2
        assert all(len(key["masked_hash"]) <= 8 for key in payload["keys"])

    print("PASS")


if __name__ == "__main__":
    _self_test()