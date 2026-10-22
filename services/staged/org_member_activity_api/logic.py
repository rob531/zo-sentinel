from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User


def mask_email(email: str) -> str:
    """Mask the local part of an email address without exposing the address."""
    if not email:
        return ""

    local, separator, domain = email.partition("@")
    if not separator:
        return "***"

    masked_local = f"{local[:1]}***{local[-2:]}"
    return f"{masked_local}@{domain}"


def get_org_member_activity(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return organization membership activity using the application database."""
    statement = (
        select(
            Org.id.label("org_id"),
            Org.name.label("org_name"),
            User.id.label("member_id"),
            User.email.label("member_email"),
            User.role.label("member_role"),
            User.created_at.label("member_created_at"),
        )
        .select_from(Org)
        .outerjoin(User, User.org_id == Org.id)
        .order_by(Org.id, User.created_at, User.id)
    )

    rows = db.execute(statement).all()
    organizations: Dict[Any, Dict[str, Any]] = {}
    total_members = 0

    for row in rows:
        org_id = row.org_id
        if org_id not in organizations:
            organizations[org_id] = {
                "org_id": org_id,
                "org_name": row.org_name,
                "member_count": 0,
                "by_role": {},
                "newest_member_at": None,
                "members": [],
            }

        organization = organizations[org_id]
        if row.member_id is None:
            continue

        role = row.member_role or "unknown"
        organization["member_count"] += 1
        organization["by_role"][role] = organization["by_role"].get(role, 0) + 1
        organization["members"].append(
            {
                "id": row.member_id,
                "email": mask_email(row.member_email or ""),
                "role": role,
                "created_at": row.member_created_at,
            }
        )

        if (
            organization["newest_member_at"] is None
            or row.member_created_at is None
            or row.member_created_at > organization["newest_member_at"]
        ):
            organization["newest_member_at"] = row.member_created_at

        total_members += 1

    return {
        "orgs": list(organizations.values()),
        "total_orgs": len(organizations),
        "total_members": total_members,
    }


def get_org_members(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_org_member_activity(db)


def get_members_by_org(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_org_member_activity(db)


def get_member_activity(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_org_member_activity(db)


def get_org_activity(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_org_member_activity(db)


def get_organization_activity(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_org_member_activity(db)


__all__ = [
    "mask_email",
    "get_org_member_activity",
    "get_org_members",
    "get_members_by_org",
    "get_member_activity",
    "get_org_activity",
    "get_organization_activity",
]


def _self_test() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from starlette.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    email VARCHAR NOT NULL,
                    org_id INTEGER,
                    role VARCHAR,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES (1, 'Example Org', CURRENT_TIMESTAMP),
                       (2, 'Second Org', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, org_id, role, created_at)
                VALUES (1, 'admin@example.com', 1, 'admin', CURRENT_TIMESTAMP),
                       (2, 'analyst@example.com', 1, 'analyst', CURRENT_TIMESTAMP),
                       (3, 'viewer@example.com', 2, 'viewer', CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    local_app = FastAPI()

    @local_app.get("/api/orgs/members")
    def org_members(db: Session = Depends(get_session)):
        return get_org_member_activity(db)

    local_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(local_app).get("/api/orgs/members")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_orgs"] == 2
    assert payload["total_members"] == 3
    assert payload["orgs"][0]["by_role"]["admin"] == 1
    assert all(
        "***" in member["email"]
        for organization in payload["orgs"]
        for member in organization["members"]
    )

    print("PASS")


if __name__ == "__main__":
    _self_test()