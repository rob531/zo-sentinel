from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_member_role_mix

router = APIRouter(prefix="/api")


class RoleCount(BaseModel):
    role: str
    count: int


class OrgRoleMix(BaseModel):
    org_id: int
    org_name: str
    users: int
    roles: dict[str, int]


class OrgMemberRoleMixResponse(BaseModel):
    total_users: int
    roles: list[RoleCount]
    orgs: list[OrgRoleMix]


@router.get(
    "/orgs/member-role-mix",
    response_model=OrgMemberRoleMixResponse,
)
def org_member_role_mix(
    session: Session = Depends(get_session),
) -> OrgMemberRoleMixResponse:
    return get_org_member_role_mix(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                    org_id INTEGER NOT NULL,
                    role VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name)
                VALUES (:id, :name)
                """
            ),
            [{"id": 1, "name": "Acme"}, {"id": 2, "name": "Globex"}],
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, org_id, role)
                VALUES (:id, :email, :org_id, :role)
                """
            ),
            [
                {"id": 1, "email": "admin@example.com", "org_id": 1, "role": "admin"},
                {"id": 2, "email": "analyst@example.com", "org_id": 1, "role": "analyst"},
                {"id": 3, "email": "viewer@example.com", "org_id": 2, "role": "viewer"},
                {"id": 4, "email": "viewer2@example.com", "org_id": 2, "role": "viewer"},
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/member-role-mix")
    response.raise_for_status()
    payload = response.json()

    assert payload["total_users"] == 4
    assert next(
        item["count"] for item in payload["roles"] if item["role"] == "admin"
    ) == 1
    print("PASS")