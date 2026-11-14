"""HTTP surface for threat association coverage reporting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_coverage

router = APIRouter(prefix="/api", tags=["threat"])


class SourceCoverage(BaseModel):
    source: str
    servers: int
    pulses: int
    stale_servers: int
    oldest_seen: str | None
    newest_seen: str | None


class CoverageResponse(BaseModel):
    total_servers_in_registry: int
    servers_with_associations: int
    coverage_pct: float
    stale_after_days: int
    by_source: list[SourceCoverage]
    generated_at: str


@router.get("/threat/coverage", response_model=CoverageResponse)
def threat_coverage(
    stale_after_days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> CoverageResponse:
    report = get_coverage(db, stale_after_days=stale_after_days)
    total_servers = int(report["total_servers_in_registry"])
    servers_with_associations = int(report["servers_with_associations"])
    coverage_pct = round(
        (servers_with_associations / total_servers * 100) if total_servers else 0.0,
        2,
    )

    return CoverageResponse(
        total_servers_in_registry=total_servers,
        servers_with_associations=servers_with_associations,
        coverage_pct=coverage_pct,
        stale_after_days=int(report["stale_after_days"]),
        by_source=[SourceCoverage.model_validate(item) for item in report["by_source"]],
        generated_at=str(report["generated_at"]),
    )