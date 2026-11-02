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

from .logic import get_risk_tier_evolution

router = APIRouter(prefix="/api", tags=["risk_tier_evolution"])


class RiskTierEvolutionResponse(BaseModel):
    days: int
    evolution: list[dict[str, int | str]]


@router.get("/risk/evolution", response_model=RiskTierEvolutionResponse)
def get_risk_tier_evolution_endpoint(
    days: int, db: Session = Depends(get_session)
) -> RiskTierEvolutionResponse:
    evolution = get_risk_tier_evolution(db, days)
    return RiskTierEvolutionResponse(days=days, evolution=evolution)