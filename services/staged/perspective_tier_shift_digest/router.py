"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_perspective_digest, get_perspective_shifts

router = APIRouter(prefix="/api/perspectives", tags=["perspective-tier-shift-digest"])


class TierTransition(BaseModel):
    old_tier: str | None
    new_tier: str | None
    count: int


class ServerMover(BaseModel):
    server_id: str
    change_count: int


class SnapshotDelta(BaseModel):
    from_taken_at: str | None
    to_taken_at: str | None
    added: list[str]
    removed: list[str]


class PerspectiveDigestResponse(BaseModel):
    perspective_id: int
    name: str
    org_id: int | None
    generated_at: str
    total_events: int
    unseen_events: int
    transitions: list[TierTransition]
    top_movers: list[ServerMover]
    snapshot_delta: SnapshotDelta


@router.get("/{perspective_id}/digest", response_model=PerspectiveDigestResponse)
def perspective_digest(
    perspective_id: int,
    db: Session = Depends(get_session),
) -> PerspectiveDigestResponse:
    return PerspectiveDigestResponse(
        **get_perspective_digest(db, perspective_id)
    )


@router.get("/{perspective_id}/shifts", response_model=PerspectiveDigestResponse)
def perspective_shifts(
    perspective_id: int,
    db: Session = Depends(get_session),
) -> PerspectiveDigestResponse:
    return PerspectiveDigestResponse(
        **get_perspective_shifts(db, perspective_id)
    )