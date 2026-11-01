"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for inclusion in the FastAPI application. The route is thin:
it injects the real Session via Depends(get_session), delegates to logic.py,
and raises a 404 when the requested server is absent.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_server_lifecycle as fetch_server_lifecycle

router = APIRouter(prefix="/api", tags=["server-scanning-lifecycle"])


class ServerLifecycleResponse(BaseModel):
    server_id: str
    name: str | None = None
    registry_source: str | None = None
    trust_score: float | None = None
    verdict: str | None = None
    confidence: float | None = None
    risk_tier: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    last_scanned: datetime | None = None
    last_assessed: datetime | None = None
    scan_count: int | None = None
    scan_stale: bool
    days_since_seen: int | None = None


@router.get(
    "/servers/{server_id}/lifecycle",
    response_model=ServerLifecycleResponse,
)
def get_server_lifecycle(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerLifecycleResponse:
    lifecycle = fetch_server_lifecycle(db, server_id)
    if lifecycle is None:
        raise HTTPException(status_code=404, detail="server not found")
    return ServerLifecycleResponse(**lifecycle)