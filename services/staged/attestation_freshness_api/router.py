from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_freshness_stats

router = APIRouter(prefix="/api", tags=["attestations"])


class FreshnessBucket(BaseModel):
    bucket: str
    count: int = Field(ge=0)


class FreshnessResponse(BaseModel):
    days: int
    buckets: List[FreshnessBucket]
    stale_count: int = Field(ge=0)
    total: int = Field(ge=0)
    generated_at: datetime


@router.get("/attestations/freshness", response_model=FreshnessResponse)
def attestation_freshness(
    days: int = Query(90, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> FreshnessResponse:
    result = get_freshness_stats(session, days)
    return FreshnessResponse(**result)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    url TEXT,
                    registry_source TEXT,
                    confidence REAL,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    meta TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    trust_score REAL,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    attested_at DATETIME NOT NULL,
                    status TEXT,
                    attestation_type TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ages = [0, 3, 10, 45, 120, 400]

        for index, age in enumerate(ages, start=1):
            server_id = f"server-{index}"
            attested_at = now - timedelta(days=age)
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (server_id, name)
                    VALUES (:server_id, :name)
                    """
                ),
                {"server_id": server_id, "name": f"Server {index}"},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_attestations
                        (server_id, attested_at, status, attestation_type)
                    VALUES
                        (:server_id, :attested_at, 'valid', 'mcp')
                    """
                ),
                {"server_id": server_id, "attested_at": attested_at},
            )

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    with Session(engine) as session:
        test_app.dependency_overrides[get_session] = lambda: session
        client = TestClient(test_app)
        response = client.get("/api/attestations/freshness", params={"days": 90})

        assert response.status_code == 200, response.text
        payload = response.json()
        counts = {item["bucket"]: item["count"] for item in payload["buckets"]}
        assert counts["0-1"] == 1, payload
        assert payload["total"] == 6, payload
        assert payload["stale_count"] == 2, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()