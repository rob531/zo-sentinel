from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session

from .logic import compute_threat_intel_coverage


router = APIRouter()


class ThreatIntelSourceCoverageSource(BaseModel):
    source: str
    count: int
    is_aggregator: bool
    distinct_pulses: int
    newest_fetched_at: Optional[datetime]


class ThreatIntelSourceCoverageIndicatorType(BaseModel):
    indicator_type: str
    count: int


class ThreatIntelSourceCoverageResponse(BaseModel):
    total_indicators: int
    by_source: List[ThreatIntelSourceCoverageSource]
    aggregator_count: int
    non_aggregator_count: int
    stale_over_24h: int
    by_indicator_type: List[ThreatIntelSourceCoverageIndicatorType]


@router.get(
    "/api/threat-intel/source-coverage",
    response_model=ThreatIntelSourceCoverageResponse,
)
def get_threat_intel_source_coverage(
    session=Depends(get_session),
) -> ThreatIntelSourceCoverageResponse:
    return compute_threat_intel_coverage(session)