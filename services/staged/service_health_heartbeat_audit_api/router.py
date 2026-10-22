from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_heartbeat_audit

router = APIRouter(prefix="/api", tags=["operations"])


class ServiceHeartbeat(BaseModel):
    service: str
    status: str
    age_seconds: float
    stale: bool
    meta: Any = None


class HeartbeatAuditResponse(BaseModel):
    services: list[ServiceHeartbeat]
    stale_count: int
    total: int


@router.get("/ops/heartbeats", response_model=HeartbeatAuditResponse)
def list_heartbeats(
    session: Session = Depends(get_session),
    threshold: str = Query(
        "300",
        description="Threshold in seconds, or a JSON/comma-separated per-service threshold map.",
    ),
) -> HeartbeatAuditResponse:
    return get_heartbeat_audit(session, threshold)


if __name__ == "__main__":
    import json
    from datetime import datetime, timedelta, timezone

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT PRIMARY KEY,
                    last_heartbeat TEXT NOT NULL,
                    status TEXT NOT NULL,
                    meta TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            (
                "fresh-service",
                (now - timedelta(seconds=5)).isoformat(),
                "healthy",
                json.dumps({"region": "us-east-1"}),
            ),
            (
                "old-service",
                (now - timedelta(minutes=10)).isoformat(),
                "healthy",
                json.dumps({"region": "us-west-2"}),
            ),
            (
                "degraded-service",
                (now - timedelta(minutes=20)).isoformat(),
                "degraded",
                json.dumps({"region": "eu-west-1"}),
            ),
        ]
        connection.execute(
            text(
                """
                INSERT INTO service_health (service, last_heartbeat, status, meta)
                VALUES (:service, :last_heartbeat, :status, :meta)
                """
            ),
            [
                {
                    "service": service,
                    "last_heartbeat": last_heartbeat,
                    "status": status,
                    "meta": meta,
                }
                for service, last_heartbeat, status, meta in rows
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/ops/heartbeats", params={"threshold": 300})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["stale_count"] == 2
        assert sum(1 for item in payload["services"] if item["stale"]) == 2
        fresh = next(item for item in payload["services"] if item["service"] == "fresh-service")
        assert fresh["stale"] is False

    print("PASS")