"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_risk_tier_trend

router = APIRouter(prefix="/api/risk-tier-trend", tags=["risk-tier-trend"])


class RiskTierTrendRequest(BaseModel):
    server_id: int


class RiskTierTrendResponse(BaseModel):
    server_id: int
    risk_tier_trend: dict[str, int]


@router.post("/trend", response_model=RiskTierTrendResponse)
def get_trend(
    request: RiskTierTrendRequest,
    db: Session = Depends(get_session)
) -> RiskTierTrendResponse:
    trend = get_risk_tier_trend(db, request.server_id)
    return RiskTierTrendResponse(server_id=request.server_id, risk_tier_trend=trend)