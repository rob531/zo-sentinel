from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


_ACTIVE_KEY_WINDOW = timedelta(days=90)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _select_creator(users: List[User]) -> Optional[User]:
    if not users:
        return None

    def creator_sort_key(user: User) -> tuple:
        role = (user.role or "").strip().lower()
        role_rank = {
            "owner": 0,
            "admin": 1,
            "administrator": 2,
            "creator": 3,
        }.get(role, 4)
        created_at = user.created_at or datetime.min.replace(tzinfo=timezone.utc)
        return role_rank, _utc_datetime(created_at), user.id

    return min(users, key=creator_sort_key)


def get_api_key_usage(
    org_id: int,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    org = db.execute(
        select(Org).where(Org.id == org_id)
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    api_keys = list(
        db.execute(
            select(ApiKey)
            .where(ApiKey.org_id == org_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.asc())
        )
        .scalars()
        .all()
    )
    users = list(
        db.execute(
            select(User)
            .where(User.org_id == org_id)
            .order_by(User.created_at.asc(), User.id.asc())
        )
        .scalars()
        .all()
    )

    creator = _select_creator(users)
    now = datetime.now(timezone.utc)
    active_since = now - _ACTIVE_KEY_WINDOW
    keys: List[Dict[str, Any]] = []

    for api_key in api_keys:
        created_at = _utc_datetime(api_key.created_at)
        age_days = max(0, (now - created_at).days)
        key_prefix = (
            api_key.key_hash[:8]
            if api_key.key_hash is not None and len(api_key.key_hash) >= 8
            else "****"
        )

        keys.append(
            {
                "key_id": api_key.id,
                "label": api_key.label,
                "key_prefix": key_prefix,
                "created_at": api_key.created_at,
                "age_days": age_days,
                "creator_email": creator.email if creator is not None else None,
                "creator_role": creator.role if creator is not None else None,
                "last_used_at": None,
            }
        )

    return {
        "org": {
            "id": org.id,
            "name": org.name,
        },
        "keys": keys,
        "total_keys": len(keys),
        "active_keys": sum(
            1
            for api_key in api_keys
            if _utc_datetime(api_key.created_at) >= active_since
        ),
    }


def get_org_api_key_usage(
    org_id: int,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_api_key_usage(org_id=org_id, db=db)


def list_org_api_keys(
    org_id: int,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_api_key_usage(org_id=org_id, db=db)


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    Org.__table__.create(test_engine)
    User.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as db:
        db.add(
            Org(
                id=101,
                name="Contract Organization",
                created_at=now,
            )
        )
        db.add_all(
            [
                User(
                    id=201,
                    email="owner@example.com",
                    org_id=101,
                    role="owner",
                    clerk_created_at=now,
                    clerk_id="clerk_owner",
                    clerk_synced_via="test",
                    created_at=now,
                    password_hash="not-a-secret-used-by-this-service",
                ),
                User(
                    id=202,
                    email="member@example.com",
                    org_id=101,
                    role="member",
                    clerk_created_at=now,
                    clerk_id="clerk_member",
                    clerk_synced_via="test",
                    created_at=now,
                    password_hash="not-a-secret-used-by-this-service",
                ),
            ]
        )
        db.add_all(
            [
                ApiKey(
                    id=301,
                    org_id=101,
                    key_hash="contract-hash-value-0123456789abcdef",
                    label="Primary",
                    created_at=now,
                ),
                ApiKey(
                    id=302,
                    org_id=101,
                    key_hash="short",
                    label="Secondary",
                    created_at=now - timedelta(days=120),
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/orgs/{org_id}/api-keys")
    def api_key_usage_endpoint(org_id: int, db: Session = Depends(get_session)):
        return get_api_key_usage(org_id=org_id, db=db)

    client = TestClient(test_app)
    response = client.get("/api/orgs/101/api-keys")
    assert response.status_code == 200
    body = response.json()
    assert len(body["keys"]) == 2
    assert "key_hash" not in response.text
    assert "contract-hash-value-0123456789abcdef" not in response.text
    assert "short" not in response.text
    assert body["keys"][0]["key_prefix"] == "contract"
    assert body["keys"][1]["key_prefix"] == "****"
    assert body["total_keys"] == 2
    assert body["active_keys"] == 1
    assert body["keys"][0]["last_used_at"] is None
    print("PASS")