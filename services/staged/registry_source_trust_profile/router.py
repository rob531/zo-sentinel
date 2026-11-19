"""HTTP surface for the registry source trust profile service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import source_trust_profile

router = APIRouter(prefix="/api/registry", tags=["registry"])


class SourceTrustProfile(BaseModel):
    registry_source: str
    server_count: int
    avg_trust_score: float | None
    verdict_counts: dict[str, int]
    risk_tier_counts: dict[str, int]
    unassessed_count: int


class SourceTrustProfileResponse(BaseModel):
    generated_at: str | datetime
    sources: list[SourceTrustProfile]


@router.get(
    "/source-trust-profile",
    response_model=SourceTrustProfileResponse,
)
def get_source_trust_profile(
    db: Session = Depends(get_session),
) -> SourceTrustProfileResponse:
    payload = source_trust_profile(db)
    return SourceTrustProfileResponse(**payload)