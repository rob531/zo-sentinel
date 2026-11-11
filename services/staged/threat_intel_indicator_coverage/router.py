from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from .logic import get_indicator_coverage


class ThreatIntelCoverageResponse(BaseModel):
    total_indicators: int
    by_indicator_type: dict[str, int]
    by_source: dict[str, int]
    distinct_pulses: int
    aggregator_indicators: int
    linked_servers: int
    mean_match_confidence: float
    stale_indicators: int


IndicatorCoverageResponse = ThreatIntelCoverageResponse

router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threat-intel/coverage",
    response_model=ThreatIntelCoverageResponse,
)
async def threat_intel_indicator_coverage(
    session: AsyncSession = Depends(get_session),
) -> ThreatIntelCoverageResponse:
    result = await get_indicator_coverage(session)
    if isinstance(result, ThreatIntelCoverageResponse):
        return result
    return ThreatIntelCoverageResponse.model_validate(result)