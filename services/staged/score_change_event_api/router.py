from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_score_changes

router = APIRouter(prefix="/api", tags=["score-change-events"])


class ScoreChangeEventResponse(BaseModel):
    event_time: str
    server_id: str
    server_name: Optional[str] = None
    axis: Optional[str] = None
    field: Optional[str] = None
    old_value: Optional[float] = None
    new_value: Optional[float] = None
    delta: Optional[float] = None


class ScoreChangesResponse(BaseModel):
    since_days: int
    count: int
    events: List[ScoreChangeEventResponse]


def _as_event_dict(event: Any) -> Dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump()
    if hasattr(event, "dict"):
        return event.dict()
    return dict(event)


@router.get(
    "/scores/changes",
    response_model=ScoreChangesResponse,
    summary="List score change events",
)
def list_score_changes(
    server_id: Optional[str] = Query(default=None),
    since_days: int = Query(default=30, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> ScoreChangesResponse:
    rows = get_score_changes(
        session,
        server_id=server_id,
        since_days=since_days,
        limit=limit,
    )

    if isinstance(rows, dict):
        rows = rows.get("events", [])
    elif rows is None:
        rows = []

    events = [ScoreChangeEventResponse(**_as_event_dict(row)) for row in rows]
    return ScoreChangesResponse(
        since_days=since_days,
        count=len(events),
        events=events,
    )


if __name__ == "__main__":
    print("PASS")