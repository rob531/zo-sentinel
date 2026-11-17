from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unseen_events

router = APIRouter(prefix="/api")


@router.get("/perspectives/events/unseen")
def unseen_events(
    perspective_id: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    return get_unseen_events(session, perspective_id, limit)