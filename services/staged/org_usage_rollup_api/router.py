from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from app.db import get_session
from app.models import ApiKey, Org, User

try:
    from .logic import get_org_usage_rollup
except ImportError:
    from logic import get_org_usage_rollup


router = APIRouter(prefix="/api")


@router.get("/orgs/usage")
async def org_usage_rollup(
    include_orgs: str | None = Query(
        None,
        description="Optional comma-separated organization IDs to include.",
    ),
    min_api_keys: int = Query(
        0,
        ge=0,
        description="Only include organizations with at least this many API keys.",
    ),
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    result = get_org_usage_rollup(
        session,
        include_orgs=include_orgs,
        min_api_keys=min_api_keys,
    )
    if inspect.isawaitable(result):
        result = await result
    return result


def _run_self_test() -> None:
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    that_app.include_router(router)

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
                    org_id VARCHAR NOT NULL,
                    role VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL,
                    email VARCHAR NOT NULL,
                    password_hash VARCHAR NOT NULL
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

        org_one = "00000000-0000-0000-0000-000000000001"
        org_two = "00000000-0000-0000-0000-000000000002"
        created_at = "2025-01-01 00:00:00"

        connection.execute(
            text("INSERT INTO orgs (id, name, created_at) VALUES (:id, :name, :created_at)"),
            [
                {"id": org_one, "name": "Organization One", "created_at": created_at},
                {"id": org_two, "name": "Organization Two", "created_at": created_at},
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (id, org_id, role, created_at, email, password_hash)
                VALUES
                    (:id, :org_id, :role, :created_at, :email, :password_hash)
                """
            ),
            [
                {
                    "id": "00000000-0000-0000-0000-000000000011",
                    "org_id": org_one,
                    "role": "admin",
                    "created_at": created_at,
                    "email": "one@example.test",
                    "password_hash": "hash-one",
                },
                {
                    "id": "00000000-0000-0000-0000-000000000012",
                    "org_id": org_one,
                    "role": "member",
                    "created_at": created_at,
                    "email": "two@example.test",
                    "password_hash": "hash-two",
                },
                {
                    "id": "00000000-0000-0000-0000-000000000013",
                    "org_id": org_two,
                    "role": "member",
                    "created_at": created_at,
                    "email": "three@example.test",
                    "password_hash": "hash-three",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys
                    (id, org_id, key_hash, label, created_at)
                VALUES
                    (:id, :org_id, :key_hash, :label, :created_at)
                """
            ),
            [
                {
                    "id": "00000000-0000-0000-0000-000000000021",
                    "org_id": org_one,
                    "key_hash": "key-hash-one",
                    "label": "Primary",
                    "created_at": created_at,
                },
                {
                    "id": "00000000-0000-0000-0000-000000000022",
                    "org_id": org_two,
                    "key_hash": "key-hash-two",
                    "label": "Primary",
                    "created_at": created_at,
                },
            ],
        )

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=that_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/api/orgs/usage")

    response = asyncio.run(request())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["orgs"]) == 2, payload
    assert payload["totals"]["org_count"] == 2, payload
    assert payload["totals"]["user_count"] == 3, payload
    assert payload["totals"]["api_key_count"] == 2, payload

    org_with_two_users = next(
        org for org in payload["orgs"] if org["user_count"] == 2
    )
    assert org_with_two_users["user_count"] == 2, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()