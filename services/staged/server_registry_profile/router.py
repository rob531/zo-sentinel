"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the service spine. The route is thin: it injects the
application Session and delegates to the service logic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_server_profile

router = APIRouter(prefix="/api", tags=["server-registry-profile"])


class AxisScoreResponse(BaseModel):
    axis_name: str
    label: str | None = None
    label_index: int | None = None
    p_top: float | None = None
    escalated: bool | None = None
    scored_at: str | None = None


class ServerProfileResponse(BaseModel):
    server_id: str
    name: str | None = None
    registry_source: str | None = None
    url: str | None = None
    description: str | None = None
    trust_score: float | None = None
    verdict: str | None = None
    verdict_reasoning: str | None = None
    confidence: float | None = None
    risk_tier: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    last_scanned: str | None = None
    scan_count: int | None = None
    axes: list[AxisScoreResponse]


@router.get("/servers/{server_id}/profile", response_model=ServerProfileResponse)
def server_profile(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerProfileResponse:
    profile = get_server_profile(db, server_id)
    return ServerProfileResponse(**profile)