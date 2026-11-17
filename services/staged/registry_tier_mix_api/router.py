from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_tier_mix

router = APIRouter(prefix="/api", tags=["registry-tier-mix"])


class SourceTierCount(BaseModel):
    source: str
    tier: str
    count: int


class RegistryTierMixResponse(BaseModel):
    total: int
    by_tier: dict[str, int]
    by_source_tier: list[SourceTierCount]
    generated_at: str


@router.get("/registry/tier-mix", response_model=RegistryTierMixResponse)
def registry_tier_mix(
    source: Optional[str] = Query(default=None),
    days: int = Query(default=90, ge=1),
    db: Session = Depends(get_session),
) -> RegistryTierMixResponse:
    return get_tier_mix(db, source=source, days=days)