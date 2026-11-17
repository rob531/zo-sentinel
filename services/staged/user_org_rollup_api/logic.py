"""logic.py -- the service's data/computation layer (one concern, one file)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User


def _as_iso_string(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_user_org_rollup(db: Session) -> Dict[str, Any]:
    """Build a per-organization user rollup from the app data layer."""
    rows = db.execute(
        select(
            User.org_id,
            User.role,
            User.created_at,
            Org.id,
            Org.name,
        )
        .outerjoin(Org, Org.id == User.org_id)
    ).all()

    organization_ids = {
        organization_id
        for organization_id, in db.execute(select(Org.id)).all()
    }

    by_org: Dict[str, Dict[str, Any]] = {}
    total_users = 0
    unassigned_users = 0

    for user_org_id, role, created_at, matched_org_id, org_name in rows:
        total_users += 1

        if user_org_id is None or matched_org_id is None or user_org_id not in organization_ids:
            unassigned_users += 1
            continue

        org_id = str(user_org_id)
        if org_id not in by_org:
            by_org[org_id] = {
                "org_id": org_id,
                "org_name": str(org_name),
                "user_count": 0,
                "role_counts": {},
                "oldest_account_created_at": _as_iso_string(created_at),
                "newest_account_created_at": _as_iso_string(created_at),
            }

        org = by_org[org_id]
        org["user_count"] += 1
        role_name = str(role) if role is not None else "unknown"
        org["role_counts"][role_name] = org["role_counts"].get(role_name, 0) + 1

        created_at_string = _as_iso_string(created_at)
        if created_at_string < org["oldest_account_created_at"]:
            org["oldest_account_created_at"] = created_at_string
        if created_at_string > org["newest_account_created_at"]:
            org["newest_account_created_at"] = created_at_string

    orgs = []
    for org_id in sorted(by_org):
        org = by_org[org_id]
        org["role_counts"] = dict(sorted(org["role_counts"].items()))
        orgs.append(org)

    return {
        "orgs": orgs,
        "org_count": len(orgs),
        "user_count": total_users,
        "unassigned_user_count": unassigned_users,
    }


def compute_user_org_rollup(db: Session) -> Dict[str, Any]:
    """Alias for callers that use the computation-oriented function name."""
    return get_user_org_rollup(db)


def build_user_org_rollup(db: Session) -> Dict[str, Any]:
    """Alias for callers that use the builder-oriented function name."""
    return get_user_org_rollup(db)


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    Org.__table__.create(test_engine)
    User.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestingSession() as test_db:
        test_db.add_all(
            [
                Org(id="org-1", name="First Organization", created_at=datetime.now(timezone.utc)),
                Org(id="org-2", name="Second Organization", created_at=datetime.now(timezone.utc)),
                User(
                    id="user-1",
                    email="one@example.com",
                    org_id="org-1",
                    role="member",
                    password_hash="test",
                    created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                User(
                    id="user-2",
                    email="two@example.com",
                    org_id="org-1",
                    role="member",
                    password_hash="test",
                    created_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
                ),
                User(
                    id="user-3",
                    email="three@example.com",
                    org_id="org-2",
                    role="admin",
                    password_hash="test",
                    created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
                ),
                User(
                    id="user-4",
                    email="four@example.com",
                    org_id="missing-org",
                    role="member",
                    password_hash="test",
                    created_at=datetime(2024, 4, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        test_db.commit()

    test_app = FastAPI()

    @test_app.get("/api/orgs/users/rollup")
    def test_rollup(db: Session = Depends(get_session)):
        return get_user_org_rollup(db)

    def override_session():
        with TestingSession() as test_db:
            yield test_db

    test_app.dependency_overrides[get_session] = override_session
    response = TestClient(test_app).get("/api/orgs/users/rollup")

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_count"] == 4
    assert payload["unassigned_user_count"] == 1
    assert payload["org_count"] == 2
    assert payload["orgs"][0]["role_counts"]["member"] == 2
    print("PASS")