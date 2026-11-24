"""HTTP surface for the organization tier portfolio API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_tier_portfolio

router = APIRouter(prefix="/api", tags=["org-tier-portfolio"])


class TopServer(BaseModel):
    server_id: str
    name: str | None = None
    trust_score: float | None = None
    last_assessed: datetime | None = None


class TierPortfolio(BaseModel):
    tier: str
    count: int
    share_pct: float
    mean_trust_score: float | None = None
    top_servers: list[TopServer] = []


class OrgTierPortfolioResponse(BaseModel):
    org_id: int | str
    org_name: str
    member_count: int
    generated_at: datetime
    tiers: list[TierPortfolio]
    total_servers: int


@router.get(
    "/orgs/{org_id}/tier-portfolio",
    response_model=OrgTierPortfolioResponse,
)
def get_org_tier_portfolio_endpoint(
    org_id: int | str,
    db: Session = Depends(get_session),
) -> Any:
    return get_org_tier_portfolio(db, org_id)