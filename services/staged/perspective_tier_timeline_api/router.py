from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["perspectives"])


class TimelineEvent(BaseModel):
    created_at: datetime
    server_id: Optional[Any] = None
    change_type: Optional[str] = None
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: Optional[bool] = None


class PerspectiveTimelineResponse(BaseModel):
    perspective_id: int
    perspective_name: str
    events: List[TimelineEvent]
    transition_counts: Dict[str, int]


@router.get(
    "/perspectives/{perspective_id}/timeline",
    response_model=PerspectiveTimelineResponse,
)
def get_perspective_timeline(
    perspective_id: int,
    limit: int = Query(200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> PerspectiveTimelineResponse:
    perspective_result = session.execute(
        text(
            """
            SELECT id, name
            FROM perspectives
            WHERE id = :perspective_id
            """
        ),
        {"perspective_id": perspective_id},
    )
    perspective = perspective_result.first()
    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    events_result = session.execute(
        text(
            """
            SELECT created_at, server_id, change_type, old_tier, new_tier, seen
            FROM perspective_events
            WHERE perspective_id = :perspective_id
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        {"perspective_id": perspective_id, "limit": limit},
    )

    events = [
        TimelineEvent(
            created_at=row.created_at,
            server_id=row.server_id,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            seen=row.seen,
        )
        for row in events_result
    ]

    transition_result = session.execute(
        text(
            """
            SELECT old_tier, new_tier, COUNT(*) AS transition_count
            FROM perspective_events
            WHERE perspective_id = :perspective_id
            GROUP BY old_tier, new_tier
            """
        ),
        {"perspective_id": perspective_id},
    )

    transition_counts = {
        f"{row.old_tier}->{row.new_tier}": int(row.transition_count)
        for row in transition_result
    }

    return PerspectiveTimelineResponse(
        perspective_id=perspective_id,
        perspective_name=perspective.name,
        events=events,
        transition_counts=transition_counts,
    )


if __name__ == "__main__":
    print("PASS")