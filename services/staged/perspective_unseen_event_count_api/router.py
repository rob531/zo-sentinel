from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unseen_event_counts

router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveUnseenEventBucket(BaseModel):
    perspective_id: Any
    change_type: str
    pending_count: int
    oldest_pending_at: datetime | None


class PerspectiveUnseenEventCountResponse(BaseModel):
    limit: int
    count: int
    total_pending: int
    buckets: list[PerspectiveUnseenEventBucket]


@router.get(
    "/perspectives/unseen-events",
    response_model=PerspectiveUnseenEventCountResponse,
)
def get_perspective_unseen_event_counts(
    limit: int = Query(default=25, ge=1, le=200),
    session: Session = Depends(get_session),
) -> PerspectiveUnseenEventCountResponse:
    payload = get_unseen_event_counts(session, limit)
    return PerspectiveUnseenEventCountResponse.model_validate(payload)