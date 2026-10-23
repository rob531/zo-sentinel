from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import get_registry_source_coverage

router = APIRouter(prefix="/api", tags=["registry"])


class SourceCoverage(BaseModel):
    registry_source: str
    server_count: int
    scored_count: int
    scored_pct: float
    avg_trust_score: float
    median_scan_count: int
    risk_tier_counts: dict[str, int]
    newest_last_seen: datetime | None


class CoverageTotals(BaseModel):
    server_count: int
    scored_count: int
    scored_pct: float
    avg_trust_score: float
    risk_tier_counts: dict[str, int]


class UnscoredSource(BaseModel):
    registry_source: str
    count: int


class RegistrySourceCoverageResponse(BaseModel):
    generated_at: datetime
    sources: list[SourceCoverage]
    totals: CoverageTotals
    unscored_by_source: list[UnscoredSource]


@router.get(
    "/registry/sources/coverage",
    response_model=RegistrySourceCoverageResponse,
    summary="Registry source coverage",
)
def registry_source_coverage(
    session: Session = Depends(get_session),
) -> RegistrySourceCoverageResponse:
    return get_registry_source_coverage(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)

    routes = {
        route.path: route
        for route in test_app.routes
        if getattr(route, "path", None) == "/api/registry/sources/coverage"
    }
    assert routes
    assert "GET" in routes["/api/registry/sources/coverage"].methods
    assert TestClient is not None
    print("PASS")