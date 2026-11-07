"""HTTP surface for the perspective watchlist feed."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_watchlist_feed

router = APIRouter(prefix="/api", tags=["perspective-watchlist-feed"])


@router.get("/perspectives/{perspective_id}/watchlist")
def perspective_watchlist(
    perspective_id: str,
    unseen_only: bool = False,
    db: Session = Depends(get_session),
) -> dict:
    return get_watchlist_feed(
        db=db,
        perspective_id=perspective_id,
        unseen_only=unseen_only,
    )