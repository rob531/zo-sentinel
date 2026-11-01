"""HTTP surface for the score-dispute rollup service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import build_dispute_rollup

router = APIRouter(prefix="/api", tags=["score-dispute-rollup"])


class DisputeRollupResponse(BaseModel):
    total: int
    filtered_total: int | None
    by_status: list[dict[str, object]]
    by_reason_category: list[dict[str, object]]
    oldest_open_created_at: str | None
    oldest_open_age_hours: float | None
    computed_at: str


@router.get("/disputes/rollup", response_model=DisputeRollupResponse)
def get_dispute_rollup(
    status: str | None = None,
    db: Session = Depends(get_session),
) -> DisputeRollupResponse:
    return DisputeRollupResponse(**build_dispute_rollup(db, status))