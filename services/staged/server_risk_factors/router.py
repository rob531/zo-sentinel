# services/staged/server_risk_factors/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk-factors"])


class RiskFactor(BaseModel):
    axis_name: str
    label: str
    p_critical: float
    p_top: float
    p_danger: float
    escalated: bool


class RiskFactorsResponse(BaseModel):
    server_id: str
    server_name: str
    risk_tier: str
    verdict: str
    top_factors: List[RiskFactor]


@router.get("/servers/{server_id}/risk-factors", response_model=RiskFactorsResponse)
def get_server_risk_factors(
    server_id: str,
    session: Session = Depends(get_session)
) -> RiskFactorsResponse:
    from .logic import compute_risk_factors
    return compute_risk_factors(session, server_id)