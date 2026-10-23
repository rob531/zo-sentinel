from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session

from .logic import get_risk_tier_fleet_rollup


class RiskTierFleetRollupResponse(BaseModel):
    total: int
    by_tier: list[dict]
    by_source: list[dict]
    unscored_count: int


router = APIRouter(prefix="/api", tags=["risk"])


@router.get("/risk/fleet-rollup", response_model=RiskTierFleetRollupResponse)
def risk_tier_fleet_rollup(
    session=Depends(get_session),
) -> RiskTierFleetRollupResponse:
    return RiskTierFleetRollupResponse(**get_risk_tier_fleet_rollup(session))