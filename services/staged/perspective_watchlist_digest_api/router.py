from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_watchlist_digest


router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveWatchlistDigestItem(BaseModel):
    perspective_id: str
    name: str
    unseen_events: int = Field(ge=0)
    changed_servers: int = Field(ge=0)
    servers_with_open_dispute: int = Field(ge=0)


class WatchlistDigestResponse(BaseModel):
    perspectives: list[PerspectiveWatchlistDigestItem]
    generated_at: datetime


@router.get(
    "/perspectives/watchlist-digest",
    response_model=WatchlistDigestResponse,
    summary="Get the watchlist digest for a perspective",
)
def get_perspective_watchlist_digest(
    perspective_id: str = Query(..., min_length=1),
    limit: int = Query(25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_watchlist_digest(session, perspective_id, limit)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    assert any(
        route.path == "/api/perspectives/watchlist-digest"
        for route in test_app.routes
    )
    print("PASS")