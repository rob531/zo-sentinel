from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session

from .logic import get_trust_score_band_histogram


router = APIRouter(
    prefix="/api",
    tags=["registry-trust-score-band-histogram"],
)


class TrustScoreBand(BaseModel):
    band: str
    count: int = Field(ge=0)
    min_score: Optional[float] = None
    max_score: Optional[float] = None


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int = Field(ge=0)


class RegistrySourceCount(BaseModel):
    registry_source: str
    count: int = Field(ge=0)


class TrustScoreBandHistogramResponse(BaseModel):
    total_servers: int = Field(ge=0)
    scored_servers: int = Field(ge=0)
    bands: List[TrustScoreBand]
    by_risk_tier: List[RiskTierCount]
    by_registry_source: List[RegistrySourceCount]


@router.get(
    "/registry/trust-score-bands",
    response_model=TrustScoreBandHistogramResponse,
)
async def get_trust_score_band_histogram_endpoint(
    session=Depends(get_session),
) -> TrustScoreBandHistogramResponse:
    result = await get_trust_score_band_histogram(session)
    return TrustScoreBandHistogramResponse.model_validate(result)