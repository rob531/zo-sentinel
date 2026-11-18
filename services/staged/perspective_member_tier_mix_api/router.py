"""HTTP surface for the perspective member tier-mix service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_perspective_tier_mix, list_perspective_tier_mixes

router = APIRouter(prefix="/api/perspectives/tier-mix", tags=["perspectives"])


class PerspectiveTierMix(BaseModel):
    perspective_id: str
    name: str | None = None
    snapshot_taken_at: str | None = None
    member_count: int
    tier_counts: dict[str, int]
    unknown_members: int
    unseen_events: int
    last_event_at: str | None = None


class PerspectiveTierMixListResponse(BaseModel):
    perspectives: list[PerspectiveTierMix]


class PerspectiveTierMixMember(BaseModel):
    server_id: str
    server_name: str | None = None
    risk_tier: str | None = None


class PerspectiveTierMixDetail(PerspectiveTierMix):
    members: list[PerspectiveTierMixMember]


@router.get("", response_model=PerspectiveTierMixListResponse)
def list_tier_mix(
    db: Session = Depends(get_session),
    perspective_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1),
) -> PerspectiveTierMixListResponse:
    return PerspectiveTierMixListResponse(
        perspectives=list_perspective_tier_mixes(
            db,
            perspective_id=perspective_id,
            limit=limit,
        )
    )


@router.get("/{perspective_id}", response_model=PerspectiveTierMixDetail)
def get_tier_mix(
    perspective_id: str,
    db: Session = Depends(get_session),
) -> PerspectiveTierMixDetail:
    return PerspectiveTierMixDetail(
        **get_perspective_tier_mix(db, perspective_id=perspective_id)
    )