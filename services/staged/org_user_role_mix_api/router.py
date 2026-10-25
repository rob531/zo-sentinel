from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import Org, User

from .logic import role_mix_rows


class RoleCount(BaseModel):
    role: str
    count: int


class OrganisationRoleMix(BaseModel):
    org_id: str
    org_name: str
    total_users: int
    roles: dict[str, int]


class UserRoleMixResponse(BaseModel):
    total_users: int
    total_orgs: int
    by_role: list[RoleCount]
    by_org: list[OrganisationRoleMix]


router = APIRouter(prefix="/api", tags=["orgs"])


@router.get("/orgs/user-role-mix", response_model=UserRoleMixResponse)
def get_user_role_mix(session=Depends(get_session)) -> UserRoleMixResponse:
    return role_mix_rows(session)


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
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE orgs ("
                "id VARCHAR PRIMARY KEY, "
                "name VARCHAR NOT NULL, "
                "created_at TIMESTAMP NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE users ("
                "id VARCHAR PRIMARY KEY, "
                "org_id VARCHAR NULL, "
                "role VARCHAR NULL, "
                "created_at TIMESTAMP NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO orgs (id, name, created_at) VALUES "
                "('org-a', 'Organisation A', CURRENT_TIMESTAMP), "
                "('org-b', 'Organisation B', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, org_id, role, created_at) VALUES "
                "('user-1', 'org-a', 'admin', CURRENT_TIMESTAMP), "
                "('user-2', 'org-a', 'admin', CURRENT_TIMESTAMP), "
                "('user-3', 'org-b', 'viewer', CURRENT_TIMESTAMP)"
            )
        )

    def override_get_session():
        with TestSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/user-role-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_users"] == 3
        assert payload["total_orgs"] == 2
        assert {"role": "admin", "count": 2} in payload["by_role"]
        assert {"role": "viewer", "count": 1} in payload["by_role"]
        assert any(entry["total_users"] == 2 for entry in payload["by_org"])
        assert "email" not in response.text
        assert "password_hash" not in response.text

    test_engine.dispose()
    print("PASS")