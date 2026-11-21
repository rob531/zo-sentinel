"""router.py -- unauthenticated MCP access rollup API surface."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unauth_rollup, get_unauth_server

router = APIRouter(prefix="/api/unauth", tags=["mcp-unauth-access"])


class ObservationResponse(BaseModel):
    url: str
    unauth_access: bool
    source: str | None = None
    updated_at: datetime
    age_days: float


class RollupResponse(BaseModel):
    total_servers: int
    by_unauth_access: dict[str, int]
    by_source: dict[str, int]
    stale_count: int
    stale_threshold_days: int
    generated_at: datetime


class ServerResponse(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    observations: list[ObservationResponse]


@router.get("/rollup", response_model=RollupResponse)
def get_rollup(
    stale_threshold_days: int = Query(30, ge=0),
    db: Session = Depends(get_session),
) -> RollupResponse:
    return RollupResponse(
        **get_unauth_rollup(db, stale_threshold_days=stale_threshold_days)
    )


@router.get("/servers/{server_id}", response_model=ServerResponse)
def get_server(
    server_id: str,
    stale_threshold_days: int = Query(30, ge=0),
    db: Session = Depends(get_session),
) -> ServerResponse:
    return ServerResponse(
        **get_unauth_server(
            db,
            server_id=server_id,
            stale_threshold_days=stale_threshold_days,
        )
    )