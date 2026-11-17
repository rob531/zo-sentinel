"""HTTP surface for server axis conflict detection."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import find_axis_conflicts

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class HotAxis(BaseModel):
    axis_name: str
    p_top: float
    escalated: bool


class AxisConflict(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    overall_p_top: float
    hot_axes: list[HotAxis]


class AxisConflictResponse(BaseModel):
    threshold: float
    count: int
    conflicts: list[AxisConflict]


@router.get("/axis-conflicts", response_model=AxisConflictResponse)
def get_axis_conflicts(
    db: Session = Depends(get_session),
    threshold: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> AxisConflictResponse:
    conflicts = find_axis_conflicts(db, threshold=threshold, limit=limit)
    return AxisConflictResponse(
        threshold=threshold,
        count=len(conflicts),
        conflicts=conflicts,
    )