from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_org_server_scope


class TierCounts(BaseModel):
    TRUSTED_GENERAL: int = 0
    ENTERPRISE_CONTROLLED: int = 0
    CAUTION_LIMITED: int = 0
    HIGH_RISK_ISOLATED: int = 0
    INSUFFICIENT: int = 0
    other: int = 0


class OrgServerScope(BaseModel):
    org_id: str
    org_name: str
    members: int
    api_keys: int
    servers: int
    tiers: TierCounts


class OrgServerScopeResponse(BaseModel):
    orgs: list[OrgServerScope] = Field(default_factory=list)
    total_orgs: int
    generated_at: str


router = APIRouter(prefix="/api")


@router.get("/orgs/server-scope", response_model=OrgServerScopeResponse)
def read_org_server_scope(
    session: Session = Depends(get_session),
) -> OrgServerScopeResponse:
    result = get_org_server_scope(session)
    if isinstance(result, OrgServerScopeResponse):
        return result
    return OrgServerScopeResponse.model_validate(result)


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id VARCHAR PRIMARY KEY,
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
                    id VARCHAR PRIMARY KEY,
                    email VARCHAR NOT NULL,
                    org_id VARCHAR,
                    role VARCHAR,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id VARCHAR PRIMARY KEY,
                    org_id VARCHAR,
                    label VARCHAR,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    registry_source VARCHAR,
                    meta TEXT,
                    risk_tier VARCHAR,
                    last_assessed DATETIME
                )
                """
            )
        )

        connection.execute(
            text(
                "INSERT INTO orgs (id, name, created_at) VALUES (:id, :name, :created_at)"
            ),
            [
                {"id": "org-a", "name": "Alpha", "created_at": datetime.now(timezone.utc)},
                {"id": "org-b", "name": "Beta", "created_at": datetime.now(timezone.utc)},
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
                    "id": "user-a1",
                    "email": "a1@example.test",
                    "org_id": "org-a",
                    "role": "member",
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": "user-a2",
                    "email": "a2@example.test",
                    "org_id": "org-a",
                    "role": "admin",
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": "user-b1",
                    "email": "b1@example.test",
                    "org_id": "org-b",
                    "role": "member",
                    "created_at": datetime.now(timezone.utc),
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, label, created_at)
                VALUES (:id, :org_id, :label, :created_at)
                """
            ),
            [
                {
                    "id": "key-a",
                    "org_id": "org-a",
                    "label": "Alpha key",
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": "key-b",
                    "org_id": "org-b",
                    "label": "Beta key",
                    "created_at": datetime.now(timezone.utc),
                },
            ],
        )

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/orgs/server-scope")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["orgs"]) == 2
    assert payload["total_orgs"] == 2

    by_id = {item["org_id"]: item for item in payload["orgs"]}
    assert by_id["org-a"]["members"] == 2
    assert by_id["org-a"]["api_keys"] == 1
    assert by_id["org-b"]["members"] == 1
    assert by_id["org-b"]["api_keys"] == 1

    print("PASS")