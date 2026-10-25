from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_heartbeat_gap

router = APIRouter(prefix="/api", tags=["registry"])


class GapServer(BaseModel):
    server_id: str
    name: str | None = None
    registry_source: str | None = None
    last_seen: datetime | None = None
    last_scanned: datetime | None = None
    age_hours: float
    scan_count: int = 0
    verdict: str | None = None
    risk_tier: str | None = None


class RegistryHeartbeatGapResponse(BaseModel):
    generated_at: datetime
    threshold_hours: int
    total_servers: int = Field(ge=0)
    never_seen: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    scan_behind_registry: int = Field(ge=0)
    gap_servers: list[GapServer] = Field(default_factory=list)


@router.get(
    "/registry/heartbeat-gap",
    response_model=RegistryHeartbeatGapResponse,
    summary="Report registry heartbeat and scan gaps",
)
def registry_heartbeat_gap(
    hours: int = Query(
        default=168,
        ge=1,
        le=24 * 365,
        description="Age threshold in hours",
    ),
    session: Session = Depends(get_session),
) -> Any:
    return get_registry_heartbeat_gap(session, hours)


__all__ = [
    "GapServer",
    "RegistryHeartbeatGapResponse",
    "router",
]