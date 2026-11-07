"""HTTP surface for attestation staleness rollups."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_staleness_rollup

router = APIRouter(prefix="/api", tags=["attestations"])


class StalenessBand(BaseModel):
    band: str
    count: int


class StaleServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str | None
    last_scored_at: str
    age_days: float


class StalenessRollupResponse(BaseModel):
    days: int
    bands: list[StalenessBand]
    stale_servers: list[StaleServer]
    total_servers: int


@router.get(
    "/attestations/staleness",
    response_model=StalenessRollupResponse,
)
def get_attestation_staleness(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> StalenessRollupResponse:
    return StalenessRollupResponse(**compute_staleness_rollup(db, days))