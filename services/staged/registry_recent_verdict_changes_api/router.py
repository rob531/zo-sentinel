from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_recent_verdict_changes

router = APIRouter(prefix="/api", tags=["registry"])


class RecentVerdictChange(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    last_assessed: str | None = None
    tier_changed: bool = False


class RecentVerdictChangesResponse(BaseModel):
    hours: int
    count: int
    changes: list[RecentVerdictChange]


@router.get(
    "/registry/verdict-changes",
    response_model=RecentVerdictChangesResponse,
)
def recent_verdict_changes(
    hours: int = Query(24, ge=1),
    limit: int = Query(100),
    session: Session = Depends(get_session),
) -> RecentVerdictChangesResponse:
    limit = max(1, min(limit, 500))
    return get_recent_verdict_changes(session, hours=hours, limit=limit)