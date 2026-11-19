"""HTTP surface for the disputes ageing queue service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_ageing_queue as _get_ageing_queue
except ImportError:  # pragma: no cover - compatibility with older logic modules
    from .logic import get_disputes_ageing_queue as _get_ageing_queue


router = APIRouter(prefix="/api", tags=["disputes", "ageing"])


class AgeBucket(BaseModel):
    bucket: str
    count: int


class OldestDispute(BaseModel):
    id: int
    server_id: str
    server_name: str | None
    reason_category: str | None
    age_days: float
    proposed_overall_risk: str | None


class AgeingQueueResponse(BaseModel):
    buckets: list[AgeBucket]
    oldest: list[OldestDispute]
    total_open: int


@router.get("/disputes/ageing", response_model=AgeingQueueResponse)
def get_ageing_queue(db: Session = Depends(get_session)) -> AgeingQueueResponse:
    result = _get_ageing_queue(db)
    return AgeingQueueResponse(**result)