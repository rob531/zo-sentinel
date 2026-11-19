from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_submitter_rollup

router = APIRouter(prefix="/api", tags=["disputes"])


class SubmitterRollup(BaseModel):
    submitted_by: str
    submitted: int
    open: int
    resolved: int
    median_resolution_hours: float | None
    proposed_tiers: list[str]


class DisputeSubmitterRollupResponse(BaseModel):
    days: int
    submitters: list[SubmitterRollup]


@router.get(
    "/disputes/submitters",
    response_model=DisputeSubmitterRollupResponse,
)
def get_dispute_submitters(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> DisputeSubmitterRollupResponse:
    rollup = get_dispute_submitter_rollup(db, days=days)
    if isinstance(rollup, dict):
        payload = rollup
    else:
        payload = {"days": days, "submitters": rollup}
    return DisputeSubmitterRollupResponse.model_validate(payload)