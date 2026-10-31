from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User

try:
    from .logic import get_api_key_hygiene as _get_api_key_hygiene
except ImportError:
    try:
        from .logic import get_org_api_key_hygiene as _get_api_key_hygiene
    except ImportError:
        from .logic import get_api_key_hygiene_data as _get_api_key_hygiene

router = APIRouter(prefix="/api")


class OrgApiKeyHygiene(BaseModel):
    org_id: int
    name: str
    key_count: int
    distinct_labels: int
    oldest_key_created_at: Optional[datetime]
    days_since_newest_key: Optional[int]
    user_count: int
    orphan_key_count: int


class ApiKeyHygieneTotals(BaseModel):
    org_count: int
    total_keys: int
    orgs_without_keys: int
    orphan_keys: int


class ApiKeyHygieneResponse(BaseModel):
    orgs: List[OrgApiKeyHygiene]
    totals: ApiKeyHygieneTotals
    generated_at: datetime


@router.get("/orgs/api-key-hygiene", response_model=ApiKeyHygieneResponse)
def get_org_api_key_hygiene(
    session: Session = Depends(get_session),
) -> ApiKeyHygieneResponse:
    return _get_api_key_hygiene(session)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    key_hash VARCHAR NOT NULL,
                    label VARCHAR,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER,
                    role VARCHAR,
                    created_at DATETIME NOT NULL,
                    clerk_created_at DATETIME,
                    clerk_id VARCHAR,
                    clerk_synced_via VARCHAR,
                    email VARCHAR,
                    password_hash VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES
                    (1, 'Alpha Org', '2024-01-01 00:00:00'),
                    (2, 'Beta Org', '2024-01-02 00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys
                    (id, org_id, key_hash, label, created_at)
                VALUES
                    (1, 1, 'hash-alpha', 'production', '2024-01-03 00:00:00'),
                    (2, 1, 'hash-beta', 'staging', '2024-01-04 00:00:00'),
                    (3, 999, 'hash-orphan', 'orphan', '2024-01-05 00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (id, org_id, role, created_at, email)
                VALUES
                    (1, 1, 'admin', '2024-01-03 00:00:00', 'admin@example.com'),
                    (2, 2, 'member', '2024-01-04 00:00:00', 'member@example.com')
                """
            )
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/api-key-hygiene")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["org_count"] == 2
    assert payload["totals"]["orphan_keys"] == 1
    assert payload["totals"]["total_keys"] == 3
    print("PASS")


if __name__ == "__main__":
    _run_self_test()