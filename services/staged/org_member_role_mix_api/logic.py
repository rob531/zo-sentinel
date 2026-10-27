from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _role_value(value: Any) -> str:
    if value is None:
        return "unassigned"
    return str(value)


def get_member_role_mix(session: Session = Depends(get_session)) -> dict[str, Any]:
    statement = (
        select(
            User.org_id,
            Org.name,
            User.role,
            func.count(User.id).label("member_count"),
            func.min(User.created_at).label("first_seen"),
            func.max(User.created_at).label("last_joined"),
        )
        .select_from(User)
        .outerjoin(Org, Org.id == User.org_id)
        .group_by(User.org_id, Org.name, User.role)
    )

    rows = session.execute(statement).all()

    orgs: dict[str, dict[str, Any]] = {}
    totals_by_role: dict[str, int] = {}

    for org_id_value, org_name_value, role_value, member_count, first_seen, last_joined in rows:
        org_id = "unassigned" if org_id_value is None else str(org_id_value)
        role = _role_value(role_value)
        count = int(member_count or 0)

        org = orgs.setdefault(
            org_id,
            {
                "org_id": org_id,
                "org_name": org_name_value,
                "roles": [],
                "total_members": 0,
            },
        )

        org["roles"].append(
            {
                "role": role,
                "count": count,
                "first_seen": _iso(first_seen),
                "last_joined": _iso(last_joined),
            }
        )
        org["total_members"] += count
        totals_by_role[role] = totals_by_role.get(role, 0) + count

    for org in orgs.values():
        org["roles"].sort(key=lambda item: item["role"])

    result_orgs = [orgs[org_id] for org_id in sorted(orgs)]
    result_totals = [
        {"role": role, "count": totals_by_role[role]}
        for role in sorted(totals_by_role)
    ]

    return {
        "orgs": result_orgs,
        "totals_by_role": result_totals,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_org_member_role_mix(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_member_role_mix(session)


def get_role_mix(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_member_role_mix(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    org_id TEXT,
                    role TEXT,
                    created_at DATETIME,
                    email TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES (:id, :name, :created_at)
                """
            ),
            [
                {
                    "id": "org-1",
                    "name": "Acme",
                    "created_at": "2024-01-01T00:00:00",
                },
                {
                    "id": "org-2",
                    "name": "Globex",
                    "created_at": "2024-01-01T00:00:00",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, org_id, role, created_at, email)
                VALUES (:id, :org_id, :role, :created_at, :email)
                """
            ),
            [
                {
                    "id": "user-1",
                    "org_id": "org-1",
                    "role": "admin",
                    "created_at": "2024-01-01T00:00:00",
                    "email": "one@example.test",
                },
                {
                    "id": "user-2",
                    "org_id": "org-1",
                    "role": "analyst",
                    "created_at": "2024-01-02T00:00:00",
                    "email": "two@example.test",
                },
                {
                    "id": "user-3",
                    "org_id": "org-2",
                    "role": "admin",
                    "created_at": "2024-01-03T00:00:00",
                    "email": "three@example.test",
                },
                {
                    "id": "user-4",
                    "org_id": "org-2",
                    "role": "viewer",
                    "created_at": "2024-01-04T00:00:00",
                    "email": "four@example.test",
                },
                {
                    "id": "user-5",
                    "org_id": None,
                    "role": "viewer",
                    "created_at": "2024-01-05T00:00:00",
                    "email": "five@example.test",
                },
            ],
        )

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.get("/api/orgs/member-role-mix")(get_member_role_mix)

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/member-role-mix")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert sum(org["total_members"] for org in payload["orgs"]) == 5
        assert any(
            item["role"] == "admin" and item["count"] == 2
            for item in payload["totals_by_role"]
        )
        assert any(
            org["org_id"] == "unassigned" and org["total_members"] == 1
            for org in payload["orgs"]
        )

    print("PASS")