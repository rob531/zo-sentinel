from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

from .logic import get_scan_staleness

router = APIRouter(prefix="/api", tags=["registry"])


class ScanStalenessBucket(BaseModel):
    bucket: str
    count: int


class StaleServer(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    risk_tier: str | None = None
    days_since_scan: int | None = None
    scan_count: int = 0


class RegistrySourceSummary(BaseModel):
    registry_source: str | None = None
    servers: int
    stale_count: int


class ScanStalenessResponse(BaseModel):
    days: int
    total_servers: int
    buckets: list[ScanStalenessBucket]
    stale_servers: list[StaleServer]
    sources: list[RegistrySourceSummary]


@router.get(
    "/registry/scan-staleness",
    response_model=ScanStalenessResponse,
    status_code=200,
)
def scan_staleness(
    days: int = Query(30, ge=1, le=3650),
    session=Depends(get_session),
) -> ScanStalenessResponse:
    return get_scan_staleness(session, days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    verdict="unknown",
                    risk_tier="unknown",
                    trust_score=0.0,
                    confidence=0.0,
                    first_seen=now,
                    last_seen=now,
                    last_scanned=None,
                    scan_count=0,
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="scanned-today",
                    name="Scanned today",
                    verdict="trusted",
                    risk_tier="low",
                    trust_score=0.9,
                    confidence=0.9,
                    first_seen=now - timedelta(days=1),
                    last_seen=now,
                    last_scanned=now,
                    scan_count=1,
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="scanned-40-days",
                    name="Scanned 40 days ago",
                    verdict="suspect",
                    risk_tier="high",
                    trust_score=0.2,
                    confidence=0.8,
                    first_seen=now - timedelta(days=60),
                    last_seen=now,
                    last_scanned=now - timedelta(days=40),
                    scan_count=3,
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="scanned-2-days",
                    name="Scanned 2 days ago",
                    verdict="trusted",
                    risk_tier="low",
                    trust_score=0.8,
                    confidence=0.9,
                    first_seen=now - timedelta(days=10),
                    last_seen=now,
                    last_scanned=now - timedelta(days=2),
                    scan_count=2,
                    registry_source="test",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-staleness?days=30")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        assert sum(bucket["count"] for bucket in payload["buckets"]) == 4
        assert any(
            bucket["bucket"] == "never_scanned" and bucket["count"] == 1
            for bucket in payload["buckets"]
        )
        assert any(
            server["server_id"] == "scanned-40-days"
            for server in payload["stale_servers"]
        )

    print("PASS")