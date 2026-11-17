"""HTTP routes for MCP server freshness SLA reporting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_freshness_overview, get_server_freshness

router = APIRouter(prefix="/api", tags=["scan-freshness"])


class FreshnessBuckets(BaseModel):
    fresh: int
    stale: int
    never_scanned: int


class ServerFreshness(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    last_scanned: Optional[datetime] = None
    last_assessed: Optional[datetime] = None
    scan_count: int = 0
    age_hours: Optional[float] = None
    state: str


class FreshnessOverviewResponse(BaseModel):
    generated_at: datetime
    sla_hours: int
    total_servers: int
    buckets: FreshnessBuckets
    servers: list[ServerFreshness]


@router.get("/servers/freshness", response_model=FreshnessOverviewResponse)
def list_server_freshness(
    db: Session = Depends(get_session),
) -> FreshnessOverviewResponse:
    return FreshnessOverviewResponse(**get_freshness_overview(db))


@router.get(
    "/servers/freshness/{server_id}",
    response_model=ServerFreshness,
)
def read_server_freshness(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerFreshness:
    result = get_server_freshness(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return ServerFreshness(**result)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestingSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    name="Fresh Server",
                    registry_source="registry",
                    risk_tier="low",
                    verdict="trusted",
                    first_seen=now - timedelta(hours=2),
                    last_seen=now - timedelta(hours=1),
                    last_assessed=now - timedelta(hours=1),
                    last_scanned=now - timedelta(hours=1),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale Server",
                    registry_source="registry",
                    risk_tier="medium",
                    verdict="review",
                    first_seen=now - timedelta(hours=105),
                    last_seen=now - timedelta(hours=100),
                    last_assessed=now - timedelta(hours=100),
                    last_scanned=now - timedelta(hours=100),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="never-scanned-server",
                    name="Never Scanned Server",
                    registry_source="registry",
                    risk_tier="unknown",
                    verdict=None,
                    first_seen=now - timedelta(hours=3),
                    last_seen=now - timedelta(hours=3),
                    last_assessed=None,
                    last_scanned=None,
                    scan_count=0,
                ),
            ]
        )
        db.commit()

    def override_get_session() -> Session:
        with TestingSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/servers/freshness")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 3, payload
        assert sum(payload["buckets"].values()) == 3, payload
        assert payload["buckets"]["never_scanned"] == 1, payload
        assert payload["buckets"]["fresh"] == 1, payload
        assert payload["buckets"]["stale"] == 1, payload

        response = client.get("/api/servers/freshness/fresh-server")
        assert response.status_code == 200, response.text
        assert response.json()["server_id"] == "fresh-server", response.json()

        response = client.get("/api/servers/freshness/unknown-server")
        assert response.status_code == 404, response.text

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()