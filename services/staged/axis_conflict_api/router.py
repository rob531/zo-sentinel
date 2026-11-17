"""HTTP surface for axis-versus-registry conflict reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import build_axis_conflict_report

router = APIRouter(prefix="/api", tags=["axis-conflicts"])


class AxisConflict(BaseModel):
    server_id: str
    registry_risk_tier: str | None
    registry_verdict: str | None
    max_axis: str
    max_p_top: float
    escalated_axes: list[str]
    conflict_kind: str


class AxisConflictCounts(BaseModel):
    total: int
    by_kind: dict[str, int]


class AxisConflictReportResponse(BaseModel):
    days: int
    conflicts: list[AxisConflict]
    counts: AxisConflictCounts


@router.get("/axis-conflicts", response_model=AxisConflictReportResponse)
def get_axis_conflicts(
    db: Session = Depends(get_session),
    days: int = Query(default=30, ge=1),
) -> AxisConflictReportResponse:
    report = build_axis_conflict_report(db, days=days)
    return AxisConflictReportResponse(**report)