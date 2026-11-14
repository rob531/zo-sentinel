"""HTTP surface for the organization user access roster service."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User

try:
    from .logic import get_org_user_access_roster as _get_org_user_access_roster
except ImportError:
    try:
        from .logic import org_user_access_roster as _get_org_user_access_roster
    except ImportError:
        def _get_org_user_access_roster(db: Session) -> dict[str, list[dict[str, Any]]]:
            rows = db.execute(
                select(Org, User)
                .outerjoin(User, User.org_id == Org.id)
                .order_by(Org.id, User.id)
            ).all()

            orgs: dict[str, dict[str, Any]] = {}
            for org, user in rows:
                entry = orgs.setdefault(
                    str(org.id),
                    {
                        "org_id": str(org.id),
                        "org_name": str(org.name),
                        "user_count": 0,
                        "roles": {"admin": 0, "analyst": 0, "viewer": 0},
                        "users": [],
                    },
                )
                if user is None:
                    continue

                role = str(user.role)
                entry["user_count"] += 1
                entry["roles"][role] = entry["roles"].get(role, 0) + 1
                created_at = user.created_at
                if isinstance(created_at, (datetime, date)):
                    created_at = created_at.isoformat()
                entry["users"].append(
                    {
                        "id": str(user.id),
                        "email": str(user.email),
                        "role": role,
                        "created_at": str(created_at or ""),
                    }
                )

            return {"orgs": list(orgs.values())}


router = APIRouter(prefix="/api", tags=["org-user-access-roster"])


class RosterUser(BaseModel):
    id: str
    email: str
    role: str
    created_at: str


class OrganizationRoster(BaseModel):
    org_id: str
    org_name: str
    user_count: int
    roles: dict[str, int]
    users: list[RosterUser]


class OrgUserAccessRosterResponse(BaseModel):
    orgs: list[OrganizationRoster]


def _string_value(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value or "")


def _normalise_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        payload = {"orgs": payload}
    if not isinstance(payload, dict):
        raise ValueError("roster service returned an invalid response")

    orgs = payload.get("orgs", [])
    normalised_orgs: list[dict[str, Any]] = []
    for org in orgs:
        org = dict(org)
        org["org_id"] = _string_value(org.get("org_id"))
        org["org_name"] = _string_value(org.get("org_name"))
        org["user_count"] = int(org.get("user_count", 0))
        org["roles"] = {
            str(role): int(count)
            for role, count in dict(org.get("roles", {})).items()
        }
        users = []
        for user in org.get("users", []):
            user = dict(user)
            user["id"] = _string_value(user.get("id"))
            user["email"] = _string_value(user.get("email"))
            user["role"] = _string_value(user.get("role"))
            user["created_at"] = _string_value(user.get("created_at"))
            users.append(user)
        org["users"] = users
        normalised_orgs.append(org)

    return {"orgs": normalised_orgs}


@router.get("/orgs/users", response_model=OrgUserAccessRosterResponse)
def get_orgs_users(db: Session = Depends(get_session)) -> OrgUserAccessRosterResponse:
    payload = _get_org_user_access_roster(db)
    return OrgUserAccessRosterResponse(**_normalise_payload(payload))


if __name__ == "__main__":
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
    Org.__table__.create(engine)
    User.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    session = TestingSession()
    session.add_all(
        [
            Org(id="org-1", name="Acme"),
            Org(id="org-2", name="Globex"),
            User(
                id="user-1",
                email="a@example.com",
                org_id="org-1",
                password_hash="test",
                role="admin",
            ),
            User(
                id="user-2",
                email="b@example.com",
                org_id="org-1",
                password_hash="test",
                role="analyst",
            ),
            User(
                id="user-3",
                email="c@example.com",
                org_id="org-1",
                password_hash="test",
                role="viewer",
            ),
            User(
                id="user-4",
                email="d@example.com",
                org_id="org-2",
                password_hash="test",
                role="admin",
            ),
            User(
                id="user-5",
                email="e@example.com",
                org_id="org-2",
                password_hash="test",
                role="viewer",
            ),
        ]
    )
    session.commit()

    def override_get_session():
        yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/users")
    assert response.status_code == 200
    body = response.json()
    assert sum(org["user_count"] for org in body["orgs"]) == 5
    assert sum(org["roles"].get("admin", 0) for org in body["orgs"]) >= 1
    print("PASS")
    session.close()
    engine.dispose()