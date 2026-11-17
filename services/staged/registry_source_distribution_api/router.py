from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_registry_source_distribution

router = APIRouter(prefix="/api", tags=["registry"])


class RegistrySourceItemResponse(BaseModel):
    registry_source: str
    server_count: int
    avg_trust_score: Optional[float]
    tier_counts: dict[str, int]
    last_seen: Optional[datetime]
    total_scans: int


class RegistrySourceDistributionResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    source_count: int
    sources: list[RegistrySourceItemResponse]


@router.get(
    "/registry/sources",
    response_model=RegistrySourceDistributionResponse,
    summary="Summarize registry servers by source",
)
async def get_registry_sources(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = get_registry_source_distribution(session)
    if inspect.isawaitable(result):
        result = await result

    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif isinstance(result, dict):
        result = dict(result)
    else:
        result = dict(result)

    fleet = result.get("fleet")
    if isinstance(fleet, dict):
        merged = dict(fleet)
        merged.update(result)
        result = merged

    result.setdefault("generated_at", datetime.now(timezone.utc))

    sources = result.get("sources") or []
    result["sources"] = sorted(
        sources,
        key=lambda source: source.get("server_count", 0),
        reverse=True,
    )
    result["source_count"] = len(result["sources"])
    result["total_servers"] = sum(
        source.get("server_count", 0) for source in result["sources"]
    )
    return result


def _run_self_test() -> None:
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    registry_table = McpServerRegistry.__table__
    registry_table.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "self-test-1",
            "name": "Self test server 1",
            "registry_source": "alpha",
            "trust_score": 0.8,
            "verdict": "trusted",
            "risk_tier": "high",
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 3,
        },
        {
            "server_id": "self-test-2",
            "name": "Self test server 2",
            "registry_source": "alpha",
            "trust_score": 0.6,
            "verdict": "trusted",
            "risk_tier": "low",
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 2,
        },
        {
            "server_id": "self-test-3",
            "name": "Self test server 3",
            "registry_source": "beta",
            "trust_score": 0.4,
            "verdict": "review",
            "risk_tier": "medium",
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 4,
        },
        {
            "server_id": "self-test-4",
            "name": "Self test server 4",
            "registry_source": "beta",
            "trust_score": 0.2,
            "verdict": "review",
            "risk_tier": "high",
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
        },
    ]

    with testing_session() as session:
        session.execute(registry_table.insert(), rows)
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with testing_session() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/registry/sources")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 2
    assert sum(source["server_count"] for source in payload["sources"]) == 4
    assert payload["total_servers"] == 4
    assert payload["source_count"] == 2

    alpha = next(
        source for source in payload["sources"] if source["registry_source"] == "alpha"
    )
    beta = next(
        source for source in payload["sources"] if source["registry_source"] == "beta"
    )
    assert abs(alpha["avg_trust_score"] - 0.7) < 0.001
    assert abs(beta["avg_trust_score"] - 0.3) < 0.001
    assert alpha["tier_counts"]["high"] == 1
    assert alpha["tier_counts"]["low"] == 1
    assert beta["tier_counts"]["medium"] == 1
    assert beta["tier_counts"]["high"] == 1
    assert alpha["total_scans"] == 5
    assert beta["total_scans"] == 5

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()