"""Router for the perspective tier-change feed."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_perspective_changes

router = APIRouter(prefix="/api", tags=["perspective-tier-change-feed"])


class PerspectiveChangeEventResponse(BaseModel):
    server_id: str
    server_name: str | None = None
    change_type: str
    old_tier: str | None = None
    new_tier: str | None = None
    seen: bool
    created_at: object


class PerspectiveChangesResponse(BaseModel):
    perspective_id: int
    perspective_name: str
    total: int
    unseen: int
    events: list[PerspectiveChangeEventResponse]
    by_change_type: dict[str, int]
    by_new_tier: dict[str, int]


@router.get(
    "/perspectives/{perspective_id}/changes",
    response_model=PerspectiveChangesResponse,
)
def get_changes(
    perspective_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> PerspectiveChangesResponse:
    result = get_perspective_changes(db, perspective_id, limit)
    return PerspectiveChangesResponse(**result)


if __name__ == "__main__":
    print("PASS")