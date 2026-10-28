from typing import Iterator, List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


class RoleCount(BaseModel):
    role: str
    count: int


class OrgRoleHeadcount(BaseModel):
    org_id: str
    org_name: str
    total_users: int
    api_keys: int
    by_role: List[RoleCount]


class RoleHeadcountResponse(BaseModel):
    orgs: List[OrgRoleHeadcount]


router = APIRouter(prefix="/api")


def get_role_headcount(session: Session) -> RoleHeadcountResponse:
    statement = (
        select(
            Org.id.label("org_id"),
            Org.name.label("org_name"),
            User.role.label("role"),
            func.count(distinct(User.id)).label("user_count"),
            func.count(distinct(ApiKey.id)).label("api_key_count"),
        )
        .select_from(Org)
        .outerjoin(User, User.org_id == Org.id)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        .group_by(Org.id, Org.name, User.role)
    )

    rows = session.execute(statement).all()
    organizations = {}

    for row in rows:
        org_id = str(row.org_id)
        org = organizations.setdefault(
            org_id,
            {
                "org_id": org_id,
                "org_name": str(row.org_name),
                "total_users": 0,
                "api_keys": int(row.api_key_count or 0),
                "by_role": [],
            },
        )

        user_count = int(row.user_count or 0)
        org["total_users"] += user_count
        if row.role is not None and user_count:
            org["by_role"].append(
                {
                    "role": str(row.role),
                    "count": user_count,
                }
            )

    orgs = sorted(
        organizations.values(),
        key=lambda item: (-item["total_users"], item["org_name"], item["org_id"]),
    )

    for org in orgs:
        org["by_role"].sort(key=lambda item: (-item["count"], item["role"]))

    return RoleHeadcountResponse(orgs=orgs)


@router.get(
    "/orgs/users/role-headcount",
    response_model=RoleHeadcountResponse,
)
def role_headcount(
    session: Session = Depends(get_session),
) -> RoleHeadcountResponse:
    return get_role_headcount(session)


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT
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
                    email TEXT,
                    created_at TEXT,
                    clerk_created_at TEXT,
                    clerk_id TEXT,
                    clerk_synced_via TEXT,
                    password_hash TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id TEXT PRIMARY KEY,
                    org_id TEXT,
                    key_hash TEXT,
                    label TEXT,
                    created_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES
                    ('org-1', 'Organization One', CURRENT_TIMESTAMP),
                    ('org-2', 'Organization Two', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (id, org_id, role, email, created_at)
                VALUES
                    ('user-1', 'org-1', 'admin', 'one@example.com', CURRENT_TIMESTAMP),
                    ('user-2', 'org-1', 'viewer', 'two@example.com', CURRENT_TIMESTAMP),
                    ('user-3', 'org-2', 'analyst', 'three@example.com', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
                VALUES ('key-1', 'org-1', 'not-a-real-hash', 'Primary', CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session() -> Iterator[Session]:
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/orgs/users/role-headcount")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["orgs"]) == 2
    assert sum(org["total_users"] for org in payload["orgs"]) == 3

    role_counts = {
        (org["org_id"], role["role"]): role["count"]
        for org in payload["orgs"]
        for role in org["by_role"]
    }
    assert role_counts == {
        ("org-1", "admin"): 1,
        ("org-1", "viewer"): 1,
        ("org-2", "analyst"): 1,
    }
    assert "one@example.com" not in response.text
    assert "two@example.com" not in response.text
    assert "three@example.com" not in response.text

    print("PASS")