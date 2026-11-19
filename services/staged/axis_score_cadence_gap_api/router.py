"""HTTP surface for axis-score cadence-gap reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_score_cadence_gap

router = APIRouter(prefix="/api", tags=["axis-score"])

GapBucket = Literal["never_scored", "partial", "complete"]


class CadenceGapBucketCounts(BaseModel):
    never_scored: int
    partial: int
    complete: int


class CadenceGapServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    axes_present: int
    axes_missing: list[str]
    last_scored_at: datetime | None = None
    gap_bucket: GapBucket


class AxisScoreCadenceGapResponse(BaseModel):
    generated_at: datetime
    buckets: CadenceGapBucketCounts
    servers: list[CadenceGapServer]


@router.get(
    "/axis-score/cadence-gap",
    response_model=AxisScoreCadenceGapResponse,
)
def get_axis_score_cadence_gap(
    db: Session = Depends(get_session),
) -> AxisScoreCadenceGapResponse:
    return AxisScoreCadenceGapResponse(**axis_score_cadence_gap(db))