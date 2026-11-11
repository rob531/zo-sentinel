from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import get_ecosystem_severity_mix

router = APIRouter(prefix="/api", tags=["vulnerability"])


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0


class SeverityMixTotals(BaseModel):
    advisory_count: int
    ecosystem_count: int
    feed_count: int
    package_count: int


class EcosystemSeverityMix(BaseModel):
    ecosystem: str
    advisory_count: int
    package_count: int
    severity_counts: SeverityCounts


class FeedAdvisoryCount(BaseModel):
    feed: str
    advisory_count: int


class EcosystemSeverityMixResponse(BaseModel):
    totals: SeverityMixTotals
    by_ecosystem: list[EcosystemSeverityMix]
    by_feed: list[FeedAdvisoryCount]
    unparsed_count: int
    generated_at: datetime


@router.get(
    "/vuln/ecosystem-severity-mix",
    response_model=EcosystemSeverityMixResponse,
)
def ecosystem_severity_mix(
    session: Session = Depends(get_session),
) -> EcosystemSeverityMixResponse:
    return get_ecosystem_severity_mix(session, VulnAdvisory)