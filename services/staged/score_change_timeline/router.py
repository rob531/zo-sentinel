from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_score_change_timeline, ScoreChangeTimelineResponse

router = APIRouter(prefix="/api", tags=["score_change_timeline"])


@router.get(
    "/scoring/timeline",
    response_model=List[ScoreChangeTimelineResponse],
    summary="Retrieve score change timeline for a server",
)
def score_change_timeline(
    server_id: str,
    days: int = 30,
    session: Session = Depends(get_session),
) -> List[ScoreChangeTimelineResponse]:
    """
    Return a timeline of axis scores for the given `server_id` limited to the
    most recent `days` days. The underlying logic handles the database query
    and response construction.
    """
    return get_score_change_timeline(session, server_id, days)