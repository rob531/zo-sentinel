from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_attestation_age_histogram

router = APIRouter(prefix="/api")


class AgeHistogramBucket(BaseModel):
    bucket_start: str
    count: int = Field(ge=0)


class AgeHistogramResponse(BaseModel):
    days: int
    bucket: Literal["day", "week", "month"]
    buckets: list[AgeHistogramBucket]
    total: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)


@router.get(
    "/attestations/age-histogram",
    response_model=AgeHistogramResponse,
)
async def attestation_age_histogram(
    days: int = Query(default=90, ge=1),
    bucket: Literal["day", "week", "month"] = Query(default="week"),
    session=Depends(get_session),
) -> AgeHistogramResponse:
    result = get_attestation_age_histogram(
        days=days,
        bucket=bucket,
        session=session,
    )
    if inspect.isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.now(timezone.utc)
    fresh_at = now - timedelta(days=1)
    stale_at = now - timedelta(days=40)

    with testing_session() as session:
        session.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    issued_at DATETIME,
                    issued DATETIME,
                    created_at DATETIME,
                    created DATETIME
                )
                """
            )
        )
        for server_id, seen_at in (
            ("fresh-server", fresh_at),
            ("stale-server", stale_at),
            ("missing-server", now - timedelta(days=2)),
        ):
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=server_id,
                    first_seen=seen_at,
                    last_seen=seen_at,
                )
            )
        session.execute(
            text(
                """
                INSERT INTO mcp_attestations (
                    id, server_id, issued_at, issued, created_at, created
                ) VALUES (
                    :id, :server_id, :issued_at, :issued_at, :issued_at, :issued_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "fresh-server",
                    "issued_at": fresh_at,
                },
                {
                    "id": 2,
                    "server_id": "stale-server",
                    "issued_at": stale_at,
                },
            ],
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/attestations/age-histogram",
            params={"days": 90, "bucket": "week"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload["buckets"], list)
        assert payload["total"] == 2
        assert payload["stale_count"] == 1
        assert payload["missing_count"] == 1

    print("PASS")