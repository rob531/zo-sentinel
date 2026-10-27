from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.db import get_session
from app.models import Perspective, PerspectiveEvent

from .logic import unseen_backlog

router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveUnseenCount(BaseModel):
    perspective_id: str
    perspective_name: str
    unseen: int
    total: int


class UnseenBacklogResponse(BaseModel):
    total_events: int
    unseen_events: int
    unseen_ratio: float
    by_change_type: dict[str, int]
    by_perspective: list[PerspectiveUnseenCount]


@router.get(
    "/perspectives/events/unseen-backlog",
    response_model=UnseenBacklogResponse,
)
def get_unseen_backlog(session=Depends(get_session)) -> UnseenBacklogResponse:
    rows_events = session.execute(
        select(
            PerspectiveEvent.id,
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.server_id,
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.seen,
            PerspectiveEvent.created_at,
        )
    ).mappings().all()

    rows_perspectives = session.execute(
        select(Perspective.id, Perspective.name)
    ).mappings().all()

    return UnseenBacklogResponse(
        **unseen_backlog(rows_events, rows_perspectives)
    )