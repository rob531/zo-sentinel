from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import ApiKey, Org, User
from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session


def get_api_key_distribution(
    session: Session,
    limit: int = 100,
    org_id: Optional[int] = None,
) -> Dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=30)

    org_rows = session.query(Org.id, Org.name).all()
    user_rows = (
        session.query(
            User.org_id.label("org_id"),
            func.count(User.id).label("user_count"),
        )
        .group_by(User.org_id)
        .all()
    )
    key_rows = (
        session.query(
            ApiKey.org_id.label("org_id"),
            func.count(ApiKey.id).label("key_count"),
            func.count(distinct(ApiKey.label)).label("distinct_labels"),
            func.min(ApiKey.created_at).label("oldest_key_created_at"),
            func.max(ApiKey.created_at).label("newest_key_created_at"),
            func.count(
                case((ApiKey.created_at >= cutoff, ApiKey.id), else_=None)
            ).label("keys_created_last_30d"),
        )
        .group_by(ApiKey.org_id)
        .all()
    )

    users_by_org = {
        row.org_id: int(row.user_count or 0)
        for row in user_rows
    }
    keys_by_org = {
        row.org_id: {
            "key_count": int(row.key_count or 0),
            "distinct_labels": int(row.distinct_labels or 0),
            "oldest_key_created_at": row.oldest_key_created_at,
            "newest_key_created_at": row.newest_key_created_at,
            "keys_created_last_30d": int(row.keys_created_last_30d or 0),
        }
        for row in key_rows
    }

    inventory: List[Dict[str, Any]] = []
    for org in org_rows:
        key_data = keys_by_org.get(
            org.id,
            {
                "key_count": 0,
                "distinct_labels": 0,
                "oldest_key_created_at": None,
                "newest_key_created_at": None,
                "keys_created_last_30d": 0,
            },
        )
        users_in_org = users_by_org.get(org.id, 0)
        key_count = key_data["key_count"]
        inventory.append(
            {
                "org_id": org.id,
                "org_name": org.name,
                "key_count": key_count,
                "distinct_labels": key_data["distinct_labels"],
                "users_in_org": users_in_org,
                "keys_per_user_ratio": (
                    float(key_count) / users_in_org if users_in_org else 0.0
                ),
                "oldest_key_created_at": key_data["oldest_key_created_at"],
                "newest_key_created_at": key_data["newest_key_created_at"],
                "keys_created_last_30d": key_data["keys_created_last_30d"],
            }
        )

    inventory.sort(key=lambda item: (-item["key_count"], item["org_id"]))

    orgs_with_keys = sum(
        1 for item in inventory if item["key_count"] > 0
    )
    key_org_ids = {
        item["org_id"] for item in inventory if item["key_count"] > 0
    }
    total_users = sum(users_by_org.values())
    users_without_keys = sum(
        count
        for user_org_id, count in users_by_org.items()
        if user_org_id not in key_org_ids
    )

    if org_id is not None:
        inventory = [item for item in inventory if item["org_id"] == org_id]

    safe_limit = min(max(int(limit), 0), 500)
    inventory = inventory[:safe_limit]

    return {
        "generated_at": generated_at,
        "totals": {
            "orgs_with_keys": orgs_with_keys,
            "total_keys": sum(int(row.key_count or 0) for row in key_rows),
            "total_users": total_users,
            "orgs_without_keys": len(org_rows) - orgs_with_keys,
            "users_without_keys": users_without_keys,
        },
        "orgs": inventory,
    }


get_api_key_org_distribution = get_api_key_distribution
build_api_key_org_distribution = get_api_key_distribution
get_org_key_distribution = get_api_key_distribution


if __name__ == "__main__":
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
    ApiKey.metadata.create_all(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False)

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as db:
        db.add_all(
            [
                Org(id=1, name="Alpha", created_at=now),
                Org(id=2, name="Beta", created_at=now),
                Org(id=3, name="Gamma", created_at=now),
                User(
                    id=1,
                    email="alpha-one@example.com",
                    org_id=1,
                    password_hash="password-hash-1",
                    role="member",
                    created_at=now,
                    clerk_created_at=now,
                    clerk_id="clerk-1",
                    clerk_synced_via="test",
                ),
                User(
                    id=2,
                    email="alpha-two@example.com",
                    org_id=1,
                    password_hash="password-hash-2",
                    role="member",
                    created_at=now,
                    clerk_created_at=now,
                    clerk_id="clerk-2",
                    clerk_synced_via="test",
                ),
                User(
                    id=3,
                    email="beta@example.com",
                    org_id=2,
                    password_hash="password-hash-3",
                    role="member",
                    created_at=now,
                    clerk_created_at=now,
                    clerk_id="clerk-3",
                    clerk_synced_via="test",
                ),
                User(
                    id=4,
                    email="gamma@example.com",
                    org_id=3,
                    password_hash="password-hash-4",
                    role="member",
                    created_at=now,
                    clerk_created_at=now,
                    clerk_id="clerk-4",
                    clerk_synced_via="test",
                ),
                ApiKey(
                    id=1,
                    org_id=1,
                    key_hash="secret-key-hash-1",
                    label="production",
                    created_at=now,
                ),
                ApiKey(
                    id=2,
                    org_id=1,
                    key_hash="secret-key-hash-2",
                    label="staging",
                    created_at=now,
                ),
                ApiKey(
                    id=3,
                    org_id=1,
                    key_hash="secret-key-hash-3",
                    label="production",
                    created_at=now - timedelta(days=40),
                ),
                ApiKey(
                    id=4,
                    org_id=2,
                    key_hash="secret-key-hash-4",
                    label="production",
                    created_at=now,
                ),
                ApiKey(
                    id=5,
                    org_id=2,
                    key_hash="secret-key-hash-5",
                    label="development",
                    created_at=now,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/orgs/api-key-distribution")
    def api_key_org_distribution_endpoint(
        limit: int = 100,
        org_id: Optional[int] = None,
        session: Session = Depends(get_session),
    ):
        return get_api_key_distribution(session, limit, org_id)

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/api-key-distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["total_keys"] == 5
        assert payload["totals"]["orgs_with_keys"] == 2
        assert payload["totals"]["orgs_without_keys"] == 1
        assert payload["orgs"][0]["key_count"] == 3
        response_text = response.text
        assert "key_hash" not in response_text
        for secret in (
            "secret-key-hash-1",
            "secret-key-hash-2",
            "secret-key-hash-3",
            "secret-key-hash-4",
            "secret-key-hash-5",
        ):
            assert secret not in response_text

    print("PASS")