"""HTTP surface for attestation freshness rollups."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_rollup

router = APIRouter(prefix="/api", tags=["attestations"])


class TierFreshness(BaseModel):
    total: int
    expiring_30d: int
    expired: int
    unattested: int


class FreshnessRollupResponse(BaseModel):
    generated_at: str
    total: int
    expiring_30d: int
    expired: int
    unattested_servers: int
    by_tier: dict[str, TierFreshness]


@router.get(
    "/attestations/freshness",
    response_model=FreshnessRollupResponse,
)
def attestation_freshness_rollup(
    db: Session = Depends(get_session),
) -> FreshnessRollupResponse:
    result = get_freshness_rollup(db)
    if not isinstance(result, Mapping):
        result = vars(result)
    payload = dict(result)
    payload.setdefault(
        "generated_at",
        datetime.now(timezone.utc).isoformat(),
    )
    return FreshnessRollupResponse(**payload)