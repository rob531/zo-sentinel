"""HTTP surface for attestation coverage over the MCP registry."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_attestation_coverage, get_stale_attestations

router = APIRouter(prefix="/api/attestations", tags=["attestations"])


class CoverageSummary(BaseModel):
    total_servers: int
    attested_servers: int
    coverage_pct: float


class SourceCoverage(BaseModel):
    registry_source: str
    total: int
    attested: int
    coverage_pct: float


class StaleServer(BaseModel):
    server_id: str
    name: str
    last_attested_at: str | None


class StaleSummary(BaseModel):
    count: int
    sample: list[StaleServer]


class CoverageResponse(BaseModel):
    days: int
    coverage: CoverageSummary
    by_source: list[SourceCoverage]
    stale: StaleSummary


class StaleResponse(BaseModel):
    count: int
    sample: list[StaleServer]


@router.get("/coverage", response_model=CoverageResponse)
def get_coverage(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    return get_attestation_coverage(db, days)


@router.get("/stale", response_model=StaleResponse)
def get_stale(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    return get_stale_attestations(db, days)