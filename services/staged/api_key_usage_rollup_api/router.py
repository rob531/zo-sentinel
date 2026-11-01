from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_api_key_usage_rollup

router = APIRouter()


class ApiKeyUsageRollupRow(BaseModel):
    month: str
    org_id: str
    org_name: str
    key_count: int


class ApiKeyUsageRollupTotals(BaseModel):
    orgs_with_keys: int
    keys: int


class ApiKeyUsageRollupResponse(BaseModel):
    months: int
    rows: list[ApiKeyUsageRollupRow]
    totals: ApiKeyUsageRollupTotals


@router.get(
    "/orgs/keys/rollup",
    response_model=ApiKeyUsageRollupResponse,
    summary="Get API key issuance and usage rollup by organisation",
)
def api_key_usage_rollup(
    months: int = Query(6, ge=1),
    org_id: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> ApiKeyUsageRollupResponse:
    result = get_api_key_usage_rollup(session, months=months, org_id=org_id)
    return ApiKeyUsageRollupResponse(**result)


def _self_test() -> None:
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
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
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
                "INSERT INTO orgs (id, name, created_at) VALUES "
                "('org-a', 'Org A', '2026-09-01'), "
                "('org-b', 'Org B', '2026-09-01'), "
                "('org-c', 'Org C', '2026-10-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO api_keys "
                "(id, org_id, key_hash, label, created_at) VALUES "
                "('key-a', 'org-a', 'hash-a', 'A', '2026-09-02'), "
                "('key-b', 'org-a', 'hash-b', 'B', '2026-10-02'), "
                "('key-c', 'org-b', 'hash-c', 'C', '2026-10-03'), "
                "('key-d', 'org-c', 'hash-d', 'D', '2026-10-04')"
            )
        )

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/keys/rollup", params={"months": 6})
        assert response.status_code == 200
        payload = response.json()
        assert payload["months"] == 6
        assert payload["totals"]["keys"] == 4
        assert sum(row["key_count"] for row in payload["rows"]) == 4
        assert all("key_hash" not in row for row in payload["rows"])

        narrowed = client.get("/api/orgs/keys/rollup", params={"months": 1})
        assert narrowed.status_code == 200
        assert narrowed.json()["months"] == 1
        assert sum(row["key_count"] for row in narrowed.json()["rows"]) == 3

    print("PASS")


if __name__ == "__main__":
    _self_test()