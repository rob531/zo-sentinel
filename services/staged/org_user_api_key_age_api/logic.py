from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User

router = APIRouter(prefix="/api")


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def get_api_key_age(session: Session, days: int = 30) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    key_rows = session.execute(
        select(
            ApiKey.org_id,
            func.count(ApiKey.id).label("key_count"),
            func.min(ApiKey.created_at).label("oldest_key_at"),
            func.max(ApiKey.created_at).label("newest_key_at"),
        )
        .where(ApiKey.created_at <= cutoff)
        .group_by(ApiKey.org_id)
    ).all()

    key_stats = {
        row.org_id: {
            "key_count": int(row.key_count),
            "oldest_key_at": _isoformat(row.oldest_key_at),
            "newest_key_at": _isoformat(row.newest_key_at),
        }
        for row in key_rows
    }

    user_rows = session.execute(
        select(
            User.org_id,
            User.role,
            func.count(User.id).label("role_count"),
        )
        .group_by(User.org_id, User.role)
        .order_by(User.org_id, User.role)
    ).all()

    user_stats: dict[Any, dict[str, int]] = {}
    for row in user_rows:
        role_counts = user_stats.setdefault(row.org_id, {})
        role = row.role if isinstance(row.role, str) and row.role else "unknown"
        role_counts[role] = role_counts.get(role, 0) + int(row.role_count)

    orgs = session.execute(
        select(Org.id, Org.name).order_by(Org.name, Org.id)
    ).all()

    return {
        "days": days,
        "orgs": [
            {
                "org_id": str(org.id),
                "org_name": org.name,
                "key_count": key_stats.get(org.id, {}).get("key_count", 0),
                "oldest_key_at": key_stats.get(org.id, {}).get("oldest_key_at"),
                "newest_key_at": key_stats.get(org.id, {}).get("newest_key_at"),
                "user_count": sum(user_stats.get(org.id, {}).values()),
                "roles": user_stats.get(org.id, {}),
            }
            for org in orgs
        ],
    }


@router.get("/tenancy/api-key-age")
def api_key_age(
    days: int = Query(default=30, ge=0, le=3650),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_api_key_age(session=session, days=days)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    old = now - timedelta(days=120)

    with TestingSession() as session:
        session.add_all(
            [
                Org(id="org-1", name="Acme", created_at=now),
                Org(id="org-2", name="Globex", created_at=now),
                User(
                    id="user-1",
                    email="admin@example.test",
                    org_id="org-1",
                    role="admin",
                    created_at=now,
                    password_hash="test",
                ),
                User(
                    id="user-2",
                    email="member@example.test",
                    org_id="org-1",
                    role="member",
                    created_at=now,
                    password_hash="test",
                ),
                User(
                    id="user-3",
                    email="viewer@example.test",
                    org_id="org-2",
                    role="viewer",
                    created_at=now,
                    password_hash="test",
                ),
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    label="primary",
                    key_hash="hash-1",
                    created_at=old,
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-1",
                    label="secondary",
                    key_hash="hash-2",
                    created_at=old - timedelta(days=10),
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/tenancy/api-key-age?days=30")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 30
    assert len(body["orgs"]) == 2

    acme = next(org for org in body["orgs"] if org["org_id"] == "org-1")
    assert acme["key_count"] == 2
    assert acme["roles"]
    assert acme["user_count"] == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()