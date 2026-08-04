from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_verdict_changes, VerdictChangeFeedResponse

router = APIRouter(prefix="/api", tags=["verdict_change_feed"])


@router.get(
    "/verdict/changes",
    response_model=VerdictChangeFeedResponse,
    summary="Retrieve recent tier‑change events",
)
def verdict_changes(
    hours: int = Query(24, ge=1, description="Look‑back window in hours"),
    session: Session = Depends(get_session),
):
    """
    Return tier‑change events that have not yet been marked as seen.

    The heavy lifting is performed in `services.staged.verdict_change_feed.logic`.
    """
    return get_verdict_changes(session, hours)