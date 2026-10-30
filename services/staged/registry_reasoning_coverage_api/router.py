from inspect import isawaitable
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_reasoning_coverage_logic


router = APIRouter(prefix="/api", tags=["registry"])


class RiskTierReasoningCoverage(BaseModel):
    tier: str
    total: int = Field(ge=0)
    with_reasoning: int = Field(ge=0)
    missing_reasoning: int = Field(ge=0)
    coverage_pct: float


class RegistryReasoningCoverageSummary(BaseModel):
    scored_total: int = Field(ge=0)
    covered_total: int = Field(ge=0)
    coverage_pct: float
    missing_reasoning_sample: List[str] = Field(default_factory=list)


class RegistryReasoningCoverageResponse(BaseModel):
    days: int
    generated_at: str
    tiers: List[RiskTierReasoningCoverage]
    summary: RegistryReasoningCoverageSummary


@router.get(
    "/registry/verdict-reasoning-coverage",
    response_model=RegistryReasoningCoverageResponse,
)
async def get_verdict_reasoning_coverage(
    days: int = Query(default=30),
    session: Session = Depends(get_session),
):
    result = get_registry_reasoning_coverage_logic(session, days)
    if isawaitable(result):
        result = await result
    return result