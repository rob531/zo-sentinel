"""HTTP surface for MCP registry health monitoring."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_health

router = APIRouter(prefix="/api", tags=["registry-health"])


class RegistrySourceHealth(BaseModel):
    registry_source: str
    total: int
    stale: int
    never_scanned: int


class RecentlySeenServer(BaseModel):
    server_id: str
    name: str
    registry_source: str
    risk_tier: str
    last_seen: datetime | None
    scan_count: int


class RegistryHealthResponse(BaseModel):
    days: int
    filter_source: str | None
    total: int
    by_source: list[RegistrySourceHealth]
    by_risk_tier: dict[str, int]
    recently_seen: list[RecentlySeenServer]
    generated_at: datetime


@router.get("/registry/health", response_model=RegistryHealthResponse)
def registry_health(
    source: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> RegistryHealthResponse:
    result: dict[str, Any] = get_registry_health(db, source=source, days=days)
    payload = dict(result)
    payload.setdefault("days", days)
    payload.setdefault("filter_source", source)
    payload.setdefault("generated_at", datetime.now(timezone.utc))
    return RegistryHealthResponse(**payload)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    now = datetime.utcnow()
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    name="Old Server",
                    registry_source="alpha",
                    first_seen=now - timedelta(days=90),
                    last_seen=now - timedelta(days=45),
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="high",
                    verdict="unknown",
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    name="Alpha Server",
                    registry_source="alpha",
                    first_seen=now - timedelta(days=20),
                    last_seen=now - timedelta(hours=1),
                    last_scanned=now - timedelta(hours=1),
                    scan_count=4,
                    risk_tier="low",
                    verdict="healthy",
                ),
                McpServerRegistry(
                    server_id="srv-3",
                    name="Beta Server",
                    registry_source="beta",
                    first_seen=now - timedelta(days=10),
                    last_seen=now - timedelta(hours=2),
                    last_scanned=now - timedelta(hours=2),
                    scan_count=2,
                    risk_tier="medium",
                    verdict="monitor",
                ),
                McpServerRegistry(
                    server_id="srv-4",
                    name="Second Beta Server",
                    registry_source="beta",
                    first_seen=now - timedelta(days=5),
                    last_seen=now - timedelta(minutes=30),
                    last_scanned=now - timedelta(minutes=30),
                    scan_count=1,
                    risk_tier="low",
                    verdict="healthy",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/health?days=30")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4, body
    assert sum(item["never_scanned"] for item in body["by_source"]) == 1, body
    assert {item["registry_source"] for item in body["by_source"]} == {"alpha", "beta"}, body
    assert len(body["recently_seen"]) == 4, body
    print("PASS")