from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# Import the core business logic for this service.
# The logic module must provide a callable with the signature:
#   get_axis_timeline(server_id: str, days: int, axis: Optional[str], db: Session)
# returning an AxisTimelineSeries instance.
from .logic import get_axis_timeline

router = APIRouter(prefix="/api/servers", tags=["axis_scores_timeline"])


class AxisTimelinePoint(BaseModel):
    scored_at: datetime
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    risk_tier: Optional[str] = None


class AxisTimelineSeries(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    series: List[AxisTimelinePoint]


@router.get(
    "/{server_id}/axis-timeline",
    response_model=AxisTimelineSeries,
    summary="Time‑series of LLM axis scores for a server",
)
def axis_timeline_endpoint(
    server_id: str,
    days: int = 30,
    axis: Optional[str] = None,
    db: Session = Depends(get_session),
) -> AxisTimelineSeries:
    """
    Return a chronological series of axis scores for the specified server.

    * **days** – look‑back window (default 30 days)
    * **axis** – optional filter for a single axis name
    """
    # Verify the server exists; the logic layer expects a valid server.
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Delegate the heavy lifting to the service‑level logic.
    return get_axis_timeline(server_id=server_id, days=days, axis=axis, db=db)