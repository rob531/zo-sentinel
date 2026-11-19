from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_advisory_exposure_by_server

router = APIRouter(prefix="/api", tags=["advisories"])


class AdvisoryExposureItem(BaseModel):
    advisory_id: str
    feed: str | None = None
    severity: str | None = None
    package: str | None = None
    match_basis: str | None = None
    match_confidence: float | None = None
    source_url: str | None = None


class ServerAdvisoryExposure(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    advisory_count: int
    max_severity: str
    critical_count: int
    high_count: int
    advisories: list[AdvisoryExposureItem]


class AdvisoryExposureTotals(BaseModel):
    servers: int
    links: int
    by_severity: dict[str, int]


class AdvisoryExposureFilters(BaseModel):
    severity: str | None = None
    ecosystem: str | None = None
    limit: int


class AdvisoryExposureResponse(BaseModel):
    servers: list[ServerAdvisoryExposure]
    totals: AdvisoryExposureTotals
    filters: AdvisoryExposureFilters


@router.get(
    "/advisories/exposure",
    response_model=AdvisoryExposureResponse,
)
def get_advisory_exposure(
    severity: str | None = Query(
        default=None,
        description="Optional comma-separated severity values.",
    ),
    ecosystem: str | None = Query(
        default=None,
        description="Optional ecosystem filter.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> AdvisoryExposureResponse:
    result = get_advisory_exposure_by_server(
        db,
        severity=severity,
        ecosystem=ecosystem,
        limit=limit,
    )
    return AdvisoryExposureResponse(**result)