"""HTTP surface for threat and vulnerability exposure reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_exposure as _get_exposure
except ImportError:
    from .logic import get_threat_exposure as _get_exposure

router = APIRouter(prefix="/api", tags=["threat-intel-exposure"])


class ServerExposureResponse(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    advisory_count: int
    max_match_confidence: float | None = None
    severities: dict[str, int]
    ecosystems: list[str]
    pulses: list[str]
    latest_linked_at: datetime | None = None


class ThreatExposureResponse(BaseModel):
    limit: int
    count: int
    servers: list[ServerExposureResponse]


@router.get(
    "/threat/exposure",
    response_model=ThreatExposureResponse,
    summary="Expose server threat and vulnerability exposure",
)
def get_threat_exposure(
    severity: str | None = Query(default=None),
    limit: int = Query(default=50),
    db: Session = Depends(get_session),
) -> ThreatExposureResponse:
    bounded_limit = min(max(limit, 1), 200)
    result: dict[str, Any] = _get_exposure(
        db,
        severity=severity,
        limit=bounded_limit,
    )
    servers = result.get("servers", [])
    return ThreatExposureResponse(
        limit=bounded_limit,
        count=len(servers),
        servers=servers,
    )