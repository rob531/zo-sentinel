from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_score_timeline, ScoreTimelineResponse

router = APIRouter(prefix="/api", tags=["score_timeline"])


@router.get(
    "/scores/timeline",
    response_model=ScoreTimelineResponse,
    summary="Retrieve a chronological series of axis scores for a server",
)
def score_timeline(
    server_id: int = Query(..., description="Identifier of the server"),
    days: int = Query(7, ge=1, description="Number of days to include in the timeline"),
    session: Session = Depends(get_session),
):
    """
    Endpoint that returns the score timeline for a given server.
    """
    result = get_score_timeline(session, server_id, days)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return result