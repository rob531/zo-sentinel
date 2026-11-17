from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_risk_tier_crosstab

router = APIRouter(prefix="/api", tags=["risk-tier-registry-crosstab"])


class RiskTierCrosstabResponse(BaseModel):
    sources: List[str]
    tiers: List[str]
    cells: List[List[int]]
    totals: Dict[str, int]
    grand_total: int


@router.get(
    "/registry/risk-tier-crosstab",
    response_model=RiskTierCrosstabResponse,
)
def get_risk_tier_registry_crosstab(
    source: Optional[str] = None,
    session: Session = Depends(get_session),
) -> RiskTierCrosstabResponse:
    return get_risk_tier_crosstab(session, source=source)