"""HTTP surface for threat intelligence coverage reporting."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_coverage

router = APIRouter(prefix="/api/threat-intel", tags=["threat-intel"])


class IndicatorTypeCoverage(BaseModel):
    indicator_type: str
    count: int
    distinct_values: int
    newest_pulse_created: Optional[datetime]
    aggregator_ratio: float


class SourceCoverage(BaseModel):
    source: str
    count: int


class ThreatIntelCoverageResponse(BaseModel):
    total: int
    by_indicator_type: list[IndicatorTypeCoverage]
    by_source: list[SourceCoverage]
    generated_at: str


@router.get("/coverage", response_model=ThreatIntelCoverageResponse)
def get_threat_intel_coverage(
    db: Session = Depends(get_session),
) -> ThreatIntelCoverageResponse:
    return ThreatIntelCoverageResponse(**get_coverage(db))