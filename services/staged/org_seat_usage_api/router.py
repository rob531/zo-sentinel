from collections.abc import Generator

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User

from .logic import get_org_seat_usage


class OrgSeatUsage(BaseModel):
    org_id: str
    name: str
    user_count: int
    by_role: dict[str, int]
    api_key_count: int
    created_at: str


class OrgSeatUsageTotals(BaseModel):
    orgs: int
    users: int
    api_keys: int


class OrgSeatUsageResponse(BaseModel):
    orgs: list[OrgSeatUsage]
    totals: OrgSeatUsageTotals


router = APIRouter(prefix="/api")


@router.get("/orgs/seats", response_model=OrgSeatUsageResponse)
def org_seat_usage(session: Session = Depends(get_session)) -> dict:
    return get_org_seat_usage(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL
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
                    password_hash VARCHAR,
                    org_id VARCHAR,
                    role VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL,
                    clerk_id VARCHAR,
                    clerk_synced_via VARCHAR,
                    clerk_created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id VARCHAR PRIMARY KEY,
                    org_id VARCHAR NOT NULL,
                    key_hash VARCHAR NOT NULL,
                    label VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL
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
                    "name": "Example Org",
                    "created_at": "2024-01-01T00:00:00",
                },
                {
                    "id": "org-2",
                    "name": "Second Org",
                    "created_at": "2024-01-02T00:00:00",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, password_hash, org_id, role, created_at,
                    clerk_id, clerk_synced_via, clerk_created_at
                )
                VALUES (
                    :id, :email, :password_hash, :org_id, :role, :created_at,
                    :clerk_id, :clerk_synced_via, :clerk_created_at
                )
                """
            ),
            [
                {
                    "id": "user-1",
                    "email": "one@example.com",
                    "password_hash": "secret-password-1",
                    "org_id": "org-1",
                    "role": "admin",
                    "created_at": "2024-01-03T00:00:00",
                    "clerk_id": None,
                    "clerk_synced_via": None,
                    "clerk_created_at": None,
                },
                {
                    "id": "user-2",
                    "email": "two@example.com",
                    "password_hash": "secret-password-2",
                    "org_id": "org-1",
                    "role": "member",
                    "created_at": "2024-01-04T00:00:00",
                    "clerk_id": None,
                    "clerk_synced_via": None,
                    "clerk_created_at": None,
                },
                {
                    "id": "user-3",
                    "email": "orphan@example.com",
                    "password_hash": "secret-password-3",
                    "org_id": None,
                    "role": "member",
                    "created_at": "2024-01-05T00:00:00",
                    "clerk_id": None,
                    "clerk_synced_via": None,
                    "clerk_created_at": None,
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
                    "id": "key-1",
                    "org_id": "org-1",
                    "key_hash": "secret-key-1",
                    "label": "Primary",
                    "created_at": "2024-01-06T00:00:00",
                },
                {
                    "id": "key-2",
                    "org_id": "org-1",
                    "key_hash": "secret-key-2",
                    "label": "Secondary",
                    "created_at": "2024-01-07T00:00:00",
                },
            ],
        )

    def override_get_session() -> Generator[Session, None, None]:
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/seats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["users"] == 3
    assert payload["totals"]["orgs"] == 2
    assert payload["totals"]["api_keys"] == 2
    assert payload["orgs"][0]["user_count"] == 2
    assert payload["orgs"][0]["api_key_count"] == 2
    assert "password_hash" not in response.text
    assert "key_hash" not in response.text
    assert "secret-password-1" not in response.text
    assert "secret-key-1" not in response.text
    print("PASS")