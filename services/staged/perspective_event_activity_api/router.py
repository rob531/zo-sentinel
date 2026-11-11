from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session

from .logic import get_perspective_event_activity_summary


router = APIRouter(prefix="/api")


class PerspectiveEventActivityTotals(BaseModel):
    events: int = Field(ge=0)
    servers: int = Field(ge=0)
    perspectives: int = Field(ge=0)
    unseen: int = Field(ge=0)


class PerspectiveEventActivityChangeType(BaseModel):
    change_type: str
    count: int = Field(ge=0)


class PerspectiveEventActivityTransition(BaseModel):
    old_tier: str
    new_tier: str
    count: int = Field(ge=0)


class PerspectiveEventActivityPerspective(BaseModel):
    perspective_id: str
    name: str
    events: int = Field(ge=0)


class PerspectiveEventActivitySummaryResponse(BaseModel):
    days: int = Field(ge=1, le=365)
    generated_at: datetime
    totals: PerspectiveEventActivityTotals
    by_change_type: List[PerspectiveEventActivityChangeType]
    by_transition: List[PerspectiveEventActivityTransition]
    by_perspective: List[PerspectiveEventActivityPerspective]


@router.get(
    "/perspectives/events/summary",
    response_model=PerspectiveEventActivitySummaryResponse,
)
def get_perspective_event_activity(
    days: int = 30,
    session=Depends(get_session),
) -> PerspectiveEventActivitySummaryResponse:
    clamped_days = max(1, min(days, 365))
    summary = get_perspective_event_activity_summary(session, clamped_days)
    payload = dict(summary)
    payload["days"] = clamped_days
    payload["generated_at"] = datetime.now(timezone.utc)
    return PerspectiveEventActivitySummaryResponse(**payload)