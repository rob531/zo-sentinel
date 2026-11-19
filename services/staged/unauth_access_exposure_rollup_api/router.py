"""HTTP surface for the unauthenticated-access exposure rollup."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unauth_access_rollup

router = APIRouter(prefix="/api", tags=["exposure"])


class ModeRollup(BaseModel):
    mode: str
    servers: int
    high_risk_servers: int


class WorstServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    unauth_access: str
    updated_at: str | None = None


class UnauthAccessRollupResponse(BaseModel):
    total_servers: int
    by_mode: list[ModeRollup]
    worst_servers: list[WorstServer]


@router.get(
    "/exposure/unauth-access-rollup",
    response_model=UnauthAccessRollupResponse,
)
def get_unauth_access_exposure_rollup(
    db: Session = Depends(get_session),
    limit: int = Query(default=25, ge=1, le=100),
) -> UnauthAccessRollupResponse:
    rollup = get_unauth_access_rollup(db, limit=limit)
    return UnauthAccessRollupResponse.parse_obj(rollup)