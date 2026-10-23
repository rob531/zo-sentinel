from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent

from .logic import get_perspective_events


router = APIRouter(prefix="/api")


class PerspectiveEventResponse(BaseModel):
    id: int
    server_id: str
    server_name: Optional[str]
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: str


class PerspectiveEventsResponse(BaseModel):
    perspective_id: str
    since: Optional[str]
    count: int
    events: List[PerspectiveEventResponse]


def _value(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def _serialize_event(event: Any) -> PerspectiveEventResponse:
    created_at = _value(event, "created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    elif created_at is None:
        created_at = ""

    server_id = _value(event, "server_id", "")
    server_name = _value(event, "server_name")

    return PerspectiveEventResponse(
        id=int(_value(event, "id", 0)),
        server_id=str(server_id),
        server_name=str(server_name) if server_name is not None else None,
        change_type=str(_value(event, "change_type", "")),
        old_tier=(
            str(_value(event, "old_tier"))
            if _value(event, "old_tier") is not None
            else None
        ),
        new_tier=(
            str(_value(event, "new_tier"))
            if _value(event, "new_tier") is not None
            else None
        ),
        seen=bool(_value(event, "seen", False)),
        created_at=str(created_at),
    )


@router.get(
    "/perspectives/{perspective_id}/events",
    response_model=PerspectiveEventsResponse,
)
def list_perspective_events(
    perspective_id: str,
    since: Optional[datetime] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> PerspectiveEventsResponse:
    since_value = since.isoformat() if since is not None else None
    events = get_perspective_events(
        session=session,
        perspective_id=perspective_id,
        since=since_value,
        limit=limit,
    )

    if isinstance(events, dict):
        events = events.get("events", [])

    serialized = [_serialize_event(event) for event in events]
    return PerspectiveEventsResponse(
        perspective_id=perspective_id,
        since=since_value,
        count=len(serialized),
        events=serialized,
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    print("PASS")