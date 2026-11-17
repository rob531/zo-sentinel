"""HTTP surface for the score dispute digest service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_digest


router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class DisputeDigestItem(BaseModel):
    id: int | str
    server_id: str
    server_name: str
    current_tier: str | None = None
    proposed_overall_risk: Any = None
    reason_category: str | None = None
    status: str
    submitted_by: str
    created_at: datetime
    age_hours: float


class DisputeDigestResponse(BaseModel):
    generated_at: datetime
    counts_by_status: dict[str, int]
    total: int
    open_count: int
    avg_age_hours_open: float | None = None
    items: list[DisputeDigestItem]


@router.get("/digest", response_model=DisputeDigestResponse)
def get_score_dispute_digest(
    db: Session = Depends(get_session),
) -> DisputeDigestResponse:
    payload = get_dispute_digest(db)
    return DisputeDigestResponse(**payload)