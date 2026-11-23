"""HTTP surface for dispute tier-shift audits."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import dispute_tier_shift_audit

router = APIRouter(prefix="/api", tags=["disputes"])


class AxisDelta(BaseModel):
    axis_name: str
    current_p_top: float | None = None
    proposed_label: str | None = None


class DisputeTierShift(BaseModel):
    dispute_id: int
    server_id: str
    server_name: str | None = None
    current_risk_tier: str | None = None
    proposed_overall_risk: str
    reason_category: str
    status: str
    submitted_by: str
    created_at: datetime
    resolved_at: datetime | None = None
    axis_deltas: list[AxisDelta]


class DisputeTierShiftAuditResponse(BaseModel):
    disputes: list[DisputeTierShift]
    count: int
    by_status: dict[str, int]


@router.get(
    "/disputes/audit",
    response_model=DisputeTierShiftAuditResponse,
)
def get_dispute_tier_shift_audit(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> DisputeTierShiftAuditResponse:
    result: Any = dispute_tier_shift_audit(db, status=status, limit=limit)

    if isinstance(result, dict):
        disputes = list(result.get("disputes", []))
        by_status = {
            str(key): int(value)
            for key, value in dict(result.get("by_status", {})).items()
        }
        count = int(result.get("count", len(disputes)))
    else:
        disputes = list(result)
        by_status = {}
        for dispute in disputes:
            dispute_status = str(dispute["status"])
            by_status[dispute_status] = by_status.get(dispute_status, 0) + 1
        count = len(disputes)

    return DisputeTierShiftAuditResponse(
        disputes=disputes,
        count=count,
        by_status=by_status,
    )