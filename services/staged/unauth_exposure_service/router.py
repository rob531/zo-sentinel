"""HTTP surface for unauthenticated-access exposure findings."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unauth_exposure

router = APIRouter(prefix="/api", tags=["unauth-exposure"])


class UnauthExposureServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    unauth_access: Optional[str] = None
    source: Optional[str] = None
    updated_at: Optional[datetime] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None


class UnauthExposureResponse(BaseModel):
    total: int
    by_access_mode: dict[str, int]
    servers: list[UnauthExposureServer]


@router.get("/exposure/unauth", response_model=UnauthExposureResponse)
def get_unauth_exposure_endpoint(
    limit: int = 100,
    tier: Optional[str] = None,
    source: Optional[str] = None,
    db: Session = Depends(get_session),
) -> UnauthExposureResponse:
    """Return MCP servers reported as reachable without credentials."""
    bounded_limit = max(1, min(limit, 500))
    result = get_unauth_exposure(
        db,
        limit=bounded_limit,
        tier=tier,
        source=source,
    )
    return UnauthExposureResponse.model_validate(result)