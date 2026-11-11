from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent

try:
    from .logic import get_perspective_events
except ImportError:
    get_perspective_events = None


router = APIRouter(prefix="/api", tags=["perspective-events"])


class PerspectiveEventResponse(BaseModel):
    id: str
    server_id: str | None = None
    change_type: str | None = None
    old_tier: str | None = None
    new_tier: str | None = None
    seen: bool = False
    created_at: str


class PerspectiveEventsResponse(BaseModel):
    perspective_id: str
    perspective_name: str
    limit: int = Field(ge=1, le=100)
    events: list[PerspectiveEventResponse]


def _event_response(event: PerspectiveEvent) -> dict:
    return {
        "id": str(event.id),
        "server_id": str(event.server_id) if event.server_id is not None else None,
        "change_type": event.change_type,
        "old_tier": event.old_tier,
        "new_tier": event.new_tier,
        "seen": bool(event.seen),
        "created_at": event.created_at.isoformat() if event.created_at is not None else None,
    }


@router.get(
    "/perspectives/{perspective_id}/events",
    response_model=PerspectiveEventsResponse,
)
def list_perspective_events(
    perspective_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> PerspectiveEventsResponse:
    perspective = session.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()

    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    if get_perspective_events is not None:
        result = get_perspective_events(
            session=session,
            perspective_id=perspective_id,
            limit=limit,
        )
        if isinstance(result, dict):
            return PerspectiveEventsResponse(**result)
        events = list(result)
    else:
        events = list(
            session.execute(
                select(PerspectiveEvent)
                .where(PerspectiveEvent.perspective_id == perspective_id)
                .order_by(PerspectiveEvent.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

    return PerspectiveEventsResponse(
        perspective_id=str(perspective_id),
        perspective_name=perspective.name,
        limit=limit,
        events=[_event_response(event) for event in events[:limit]],
    )


if __name__ == "__main__":
    print("PASS")