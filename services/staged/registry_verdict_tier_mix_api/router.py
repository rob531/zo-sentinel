from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_verdict_tier_mix


router = APIRouter(prefix="/api", tags=["registry"])


class VerdictTierBucket(BaseModel):
    verdict: str
    risk_tier: str
    count: int
    pct: float


class VerdictTierMixResponse(BaseModel):
    total: int
    buckets: list[VerdictTierBucket]
    generated_at: datetime


@router.get(
    "/registry/verdict-tier-mix",
    response_model=VerdictTierMixResponse,
    summary="Get fleet-wide verdict and risk-tier distribution",
)
def get_verdict_tier_mix_endpoint(
    source: str | None = Query(
        default=None,
        description="Optional registry source filter",
    ),
    session: Session = Depends(get_session),
) -> VerdictTierMixResponse:
    return get_verdict_tier_mix(session=session, source=source)