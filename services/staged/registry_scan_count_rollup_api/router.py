from __future__ import annotations

from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_registry_scan_count_rollup

router = APIRouter(prefix="/api")


class RegistrySourceScanRollup(BaseModel):
    registry_source: str
    servers: int
    total_scans: int
    avg_scans: float
    max_scans: int


class TopScannedServer(BaseModel):
    server_id: str
    name: str
    scan_count: int
    risk_tier: str | None = None
    last_scanned: datetime | None = None


class RegistryScanCountRollupResponse(BaseModel):
    sources: list[RegistrySourceScanRollup]
    top_servers: list[TopScannedServer]
    total_servers: int


@router.get(
    "/registry/scan-counts",
    response_model=RegistryScanCountRollupResponse,
)
async def get_registry_scan_counts(
    session: Session = Depends(get_session),
) -> RegistryScanCountRollupResponse:
    result: Any = get_registry_scan_count_rollup(session)
    if isawaitable(result):
        result = await result

    if isinstance(result, BaseModel):
        result = result.model_dump()

    response = RegistryScanCountRollupResponse.model_validate(result)
    response.sources.sort(key=lambda source: source.total_scans, reverse=True)
    response.top_servers.sort(key=lambda server: server.scan_count, reverse=True)
    response.top_servers = response.top_servers[:20]
    return response


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    rows = [
        McpServerRegistry(
            server_id="server-zero",
            name="Zero-scan server",
            registry_source="source-zero",
            risk_tier="low",
            trust_score=100.0,
            scan_count=0,
            last_scanned=now,
            last_seen=now,
            first_seen=now,
            verdict="unknown",
        ),
        McpServerRegistry(
            server_id="server-five",
            name="Five-scan server",
            registry_source="source-five",
            risk_tier="medium",
            trust_score=75.0,
            scan_count=5,
            last_scanned=now,
            last_seen=now,
            first_seen=now,
            verdict="monitor",
        ),
        McpServerRegistry(
            server_id="server-twelve",
            name="Twelve-scan server",
            registry_source="source-twelve",
            risk_tier="high",
            trust_score=50.0,
            scan_count=12,
            last_scanned=now,
            last_seen=now,
            first_seen=now,
            verdict="block",
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-counts")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 3, payload
    assert sum(source["total_scans"] > 0 for source in payload["sources"]) == 2, payload
    assert payload["top_servers"][0]["scan_count"] == 12, payload
    assert len(payload["top_servers"]) <= 20, payload

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")