from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User


class RoleCount(BaseModel):
    role: str
    count: int


class OrgMemberRoleMix(BaseModel):
    org_id: int
    org_name: str
    users: int
    roles: dict[str, int]


class MemberRoleMixResponse(BaseModel):
    total_users: int
    roles: list[RoleCount]
    orgs: list[OrgMemberRoleMix]


ROLE_MIX_SQL = text(
    """
    SELECT
        o.id AS org_id,
        o.name AS org_name,
        u.role AS role_name,
        COUNT(u.id) AS role_count
    FROM orgs AS o
    LEFT JOIN users AS u
        ON u.org_id = o.id
    GROUP BY o.id, o.name, u.role
    ORDER BY o.id, u.role
    """
)


def get_member_role_mix(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(ROLE_MIX_SQL).mappings().all()

    orgs: list[dict[str, Any]] = []
    global_roles: defaultdict[str, int] = defaultdict(int)
    total_users = 0

    for row in rows:
        role = row["role_name"]
        count = int(row["role_count"] or 0)
        org_id = int(row["org_id"])
        org_name = str(row["org_name"])

        org_roles = orgs[-1]["roles"] if orgs and orgs[-1]["org_id"] == org_id else {}
        if not orgs or orgs[-1]["org_id"] != org_id:
            orgs.append(
                {
                    "org_id": org_id,
                    "org_name": org_name,
                    "users": 0,
                    "roles": org_roles,
                }
            )

        orgs[-1]["users"] += count
        if role is not None:
            org_roles[str(role)] = count
            global_roles[str(role)] += count
            total_users += count

    payload = {
        "total_users": total_users,
        "roles": [
            {"role": role, "count": count}
            for role, count in sorted(global_roles.items())
        ],
        "orgs": orgs,
    }
    return MemberRoleMixResponse(**payload).model_dump()


build_member_role_mix = get_member_role_mix
get_org_member_role_mix = get_member_role_mix


router = APIRouter(prefix="/api")


@router.get("/orgs/member-role-mix", response_model=MemberRoleMixResponse)
def member_role_mix(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_member_role_mix(session)


def run_contract() -> dict[str, Any]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL,
                    org_id INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES
                    (1, 'Acme', '2024-01-01 00:00:00'),
                    (2, 'Globex', '2024-01-02 00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, org_id, role, created_at)
                VALUES
                    (1, 'admin@example.com', 1, 'admin', '2024-01-03 00:00:00'),
                    (2, 'analyst@example.com', 1, 'analyst', '2024-01-04 00:00:00'),
                    (3, 'viewer@example.com', 1, 'viewer', '2024-01-05 00:00:00'),
                    (4, 'viewer2@example.com', 2, 'viewer', '2024-01-06 00:00:00')
                """
            )
        )

    session = session_factory()
    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        yield session

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/orgs/member-role-mix")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_users"] == 4
            assert next(
                item["count"]
                for item in payload["roles"]
                if item["role"] == "admin"
            ) == 1
            return payload
    finally:
        session.close()
        engine.dispose()


run_self_test = run_contract


def main() -> dict[str, Any]:
    payload = run_contract()
    print("PASS")
    return payload


if __name__ == "__main__":
    main()