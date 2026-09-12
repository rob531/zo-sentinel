from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_server_verdict_timeline
from .schemas import ServerVerdictTimelineResponse

router = APIRouter(prefix="/api", tags=["server_verdict_timeline"])


@router.get(
    "/servers/{server_id}/verdict-timeline",
    response_model=ServerVerdictTimelineResponse,
    name="server_verdict_timeline",
)
def server_verdict_timeline_endpoint(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerVerdictTimelineResponse:
    """
    Return the chronological series of axis-label + risk-tier assignments for a given server.
    Each series entry corresponds to one scored_at bucket across all 7 axis rows.
    """
    result = get_server_verdict_timeline(server_id, db)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return result
