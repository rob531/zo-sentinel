"""HTTP surface for cadence registry freshness reporting."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import registry_coverage

router = APIRouter(prefix="/api", tags=["freshness"])


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    servers: int
    never_assessed: int
    stale_assessed: int
    missing_verdict: int
    median_scan_count: float


class RegistryCoverageTotals(BaseModel):
    servers: int
    never_assessed: int


class RegistryCoverageResponse(BaseModel):
    generated_at: str
    sources: list[RegistrySourceCoverage]
    totals: RegistryCoverageTotals


@router.get(
    "/freshness/registry-coverage",
    response_model=RegistryCoverageResponse,
)
def get_registry_coverage(
    db: Session = Depends(get_session),
) -> RegistryCoverageResponse:
    coverage = registry_coverage(db)

    if hasattr(coverage, "model_dump"):
        data = coverage.model_dump()
    elif is_dataclass(coverage):
        data = asdict(coverage)
    else:
        data = dict(coverage)

    generated_at = data.get("generated_at")
    if isinstance(generated_at, datetime):
        generated_at = generated_at.isoformat()
    elif not generated_at:
        generated_at = datetime.now(timezone.utc).isoformat()

    sources = [
        source.model_dump()
        if isinstance(source, BaseModel)
        else dict(source)
        for source in data.get("sources", [])
    ]

    totals = data.get("totals")
    if isinstance(totals, BaseModel):
        totals = totals.model_dump()
    elif totals is not None:
        totals = dict(totals)
    else:
        totals = {}

    totals.setdefault("servers", sum(source["servers"] for source in sources))
    totals.setdefault(
        "never_assessed",
        sum(source["never_assessed"] for source in sources),
    )

    return RegistryCoverageResponse(
        generated_at=generated_at,
        sources=sources,
        totals=totals,
    )


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale = now - timedelta(days=45)
    rows = [
        McpServerRegistry(
            server_id="freshness-a-1",
            name="Freshness A1",
            registry_source="source-a",
            last_assessed=now,
            last_seen=now,
            last_scanned=now,
            scan_count=1,
            risk_tier="low",
            verdict="trusted",
        ),
        McpServerRegistry(
            server_id="freshness-a-2",
            name="Freshness A2",
            registry_source="source-a",
            last_assessed=stale,
            last_seen=now,
            last_scanned=now,
            scan_count=3,
            risk_tier="medium",
            verdict="review",
        ),
        McpServerRegistry(
            server_id="freshness-a-3",
            name="Freshness A3",
            registry_source="source-a",
            last_assessed=None,
            last_seen=now,
            last_scanned=now,
            scan_count=5,
            risk_tier="unknown",
            verdict="unassessed",
        ),
        McpServerRegistry(
            server_id="freshness-b-1",
            name="Freshness B1",
            registry_source="source-b",
            last_assessed=now,
            last_seen=now,
            last_scanned=now,
            scan_count=2,
            risk_tier="low",
            verdict="trusted",
        ),
        McpServerRegistry(
            server_id="freshness-b-2",
            name="Freshness B2",
            registry_source="source-b",
            last_assessed=stale,
            last_seen=now,
            last_scanned=now,
            scan_count=4,
            risk_tier="high",
            verdict="restricted",
        ),
        McpServerRegistry(
            server_id="freshness-b-3",
            name="Freshness B3",
            registry_source="source-b",
            last_assessed=now,
            last_seen=now,
            last_scanned=now,
            scan_count=6,
            risk_tier="medium",
            verdict=None,
        ),
    ]

    with TestSession() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/registry-coverage")
        assert response.status_code == 200, response.text
        payload: dict[str, Any] = response.json()
        assert len(payload["sources"]) == 2
        assert payload["totals"]["never_assessed"] == 1
        for source in payload["sources"]:
            assert source["median_scan_count"] is not None
            assert isinstance(source["median_scan_count"], (float, int))

    print("PASS")