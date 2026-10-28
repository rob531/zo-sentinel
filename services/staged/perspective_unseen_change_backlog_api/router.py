from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unseen_change_events

router = APIRouter(prefix="/api")

@router.get("/perspectives/changes/unseen")
def list_unseen_changes(
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict:
    return get_unseen_change_events(session=session, limit=limit)