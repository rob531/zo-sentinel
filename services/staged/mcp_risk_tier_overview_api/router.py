# services/staged/mcp_risk_tier_overview_api/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_overview

router = APIRouter(prefix="/api")


class TopServer(BaseModel):
    server_id: str
    name: str
    confidence: float


class TierOverview(BaseModel):
    tier: str
    count: int
    median_confidence: Optional[float]
    top_servers: List[TopServer]


class RiskOverviewResponse(BaseModel):
    total: int
    tiers: List[TierOverview]


@router.get("/risk/overview", response_model=RiskOverviewResponse)
def risk_overview(session: Session = Depends(get_session)):
    return get_risk_tier_overview(session)