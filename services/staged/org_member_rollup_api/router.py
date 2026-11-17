from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session

from .logic import get_org_member_rollup as _get_org_member_rollup

router = APIRouter(prefix="/api", tags=["orgs"])


class RoleMemberCounts(BaseModel):
    admin: int = 0
    analyst: int = 0
    viewer: int = 0


class ApiKeyAgeCounts(BaseModel):
    older_than_90: int = 0
    newer_or_equal_90: int = 0


class OrgMemberRollup(BaseModel):
    org_id: str
    name: str
    created_at: str | None = None
    members: int = 0
    by_role: RoleMemberCounts = Field(default_factory=RoleMemberCounts)
    distinct_roles: int = 0
    api_keys: int = 0
    api_key_ages_days: ApiKeyAgeCounts = Field(default_factory=ApiKeyAgeCounts)


class OrgMemberRollupResponse(BaseModel):
    orgs: list[OrgMemberRollup]
    total_orgs: int
    orphaned_users: int


@router.get("/orgs/member-rollup", response_model=OrgMemberRollupResponse)
def get_org_member_rollup(
    session: Session = Depends(get_session),
) -> OrgMemberRollupResponse:
    result = _get_org_member_rollup(session)
    if isinstance(result, BaseModel):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    normalized_orgs = []
    for org in result.get("orgs", []):
        if not isinstance(org, dict):
            org = org.model_dump() if isinstance(org, BaseModel) else dict(org)

        by_role = org.get("by_role") or {}
        org["by_role"] = {
            "admin": int(by_role.get("admin", 0)),
            "analyst": int(by_role.get("analyst", 0)),
            "viewer": int(by_role.get("viewer", 0)),
        }

        api_key_ages = org.get("api_key_ages_days") or {}
        org["api_key_ages_days"] = {
            "older_than_90": int(api_key_ages.get("older_than_90", 0)),
            "newer_or_equal_90": int(api_key_ages.get("newer_or_equal_90", 0)),
        }
        normalized_orgs.append(org)

    return OrgMemberRollupResponse(
        orgs=normalized_orgs,
        total_orgs=int(result.get("total_orgs", len(normalized_orgs))),
        orphaned_users=int(result.get("orphaned_users", 0)),
    )


def _run_self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
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
                    email TEXT,
                    org_id INTEGER,
                    role TEXT,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER,
                    key_hash TEXT,
                    label TEXT,
                    created_at DATETIME
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
                {"id": 1, "name": "Example Org", "created_at": "2024-01-01T00:00:00"},
                {"id": 2, "name": "Second Org", "created_at": "2024-02-01T00:00:00"},
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, org_id, role, created_at)
                VALUES (:id, :email, :org_id, :role, :created_at)
                """
            ),
            [
                {
                    "id": 1,
                    "email": "admin@example.com",
                    "org_id": 1,
                    "role": "admin",
                    "created_at": "2024-01-02T00:00:00",
                },
                {
                    "id": 2,
                    "email": "viewer@example.com",
                    "org_id": 1,
                    "role": "viewer",
                    "created_at": "2024-01-03T00:00:00",
                },
                {
                    "id": 3,
                    "email": "orphan@example.com",
                    "org_id": 999,
                    "role": "analyst",
                    "created_at": "2024-01-04T00:00:00",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
                VALUES (:id, :org_id, :key_hash, :label, :created_at)
                """
            ),
            [
                {
                    "id": 1,
                    "org_id": 1,
                    "key_hash": "hash-1",
                    "label": "primary",
                    "created_at": "2024-01-05T00:00:00",
                },
                {
                    "id": 2,
                    "org_id": 2,
                    "key_hash": "hash-2",
                    "label": "secondary",
                    "created_at": "2024-01-06T00:00:00",
                },
            ],
        )

    testing_session = TestingSession()

    def override_get_session():
        yield testing_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/member-rollup")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_orgs"] == 2, payload
        assert payload["orphaned_users"] == 1, payload
        assert payload["orgs"][0]["members"] == 2, payload

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()