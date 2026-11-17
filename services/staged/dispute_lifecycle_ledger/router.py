"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for spine mounting. Thin: injects the real Session via
Depends(get_session) and delegates to the service logic.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import lifecycle as build_lifecycle

router = APIRouter(prefix="/api", tags=["disputes"])


class LifecycleStatus(BaseModel):
    status: str
    count: int
    oldest_created_at: datetime | None


class LifecycleDispute(BaseModel):
    id: int
    server_id: int
    status: str
    reason_category: str
    proposed_overall_risk: float | None
    created_at: datetime
    resolved_at: datetime | None


class LifecycleResponse(BaseModel):
    total: int
    by_status: list[LifecycleStatus]
    open_count: int
    resolved_count: int
    median_open_age_hours: float | None
    disputes: list[LifecycleDispute]


@router.get("/disputes/lifecycle", response_model=LifecycleResponse)
def lifecycle(db: Session = Depends(get_session)) -> LifecycleResponse:
    payload: dict[str, Any] = build_lifecycle(db)
    return LifecycleResponse(**payload)