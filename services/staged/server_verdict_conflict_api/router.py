"""HTTP surface for registry-verdict conflict detection."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_verdict_conflicts

router = APIRouter(prefix="/api", tags=["verdict-conflicts"])


class VerdictConflict(BaseModel):
    server_id: str
    name: str | None = None
    registry_verdict: str | None = None
    registry_risk_tier: str | None = None
    derived_tier: str
    gap: Literal["verdict_only", "tier_only", "tier_mismatch"]


class VerdictConflictResponse(BaseModel):
    days: int
    count: int
    conflicts: list[VerdictConflict]
    coverage: dict[str, Any]


def _axis_coverage(db: Session) -> dict[str, Any]:
    registry_ids = set(
        db.scalars(select(McpServerRegistry.server_id)).all()
    )
    scored_ids = set(
        db.scalars(
            select(McpLlmAxisScore.server_id).where(
                McpLlmAxisScore.axis_name == "overall_risk"
            )
        ).all()
    )
    missing_ids = sorted(registry_ids - scored_ids)
    covered_count = len(registry_ids & scored_ids)

    return {
        "total_servers": len(registry_ids),
        "with_axis_score": covered_count,
        "missing_axis_count": len(missing_ids),
        "missing_axis": missing_ids,
        "missing_axis_servers": missing_ids,
    }


@router.get("/verdict/conflicts", response_model=VerdictConflictResponse)
def verdict_conflicts(
    days: int = Query(default=30, ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> VerdictConflictResponse:
    rows = get_verdict_conflicts(db, days=days, limit=limit)
    conflicts = [VerdictConflict.model_validate(row) for row in rows]

    return VerdictConflictResponse(
        days=days,
        count=len(conflicts),
        conflicts=conflicts,
        coverage=_axis_coverage(db),
    )