"""HTTP surface for the dispute outcome summary service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

from .logic import summarize_dispute_outcomes

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class ReasonCount(BaseModel):
    reason_category: str
    count: int


class OutcomeSummaryResponse(BaseModel):
    days: int
    total: int
    by_status: dict[str, int]
    by_reason: list[ReasonCount]
    by_proposed_risk: dict[str, int]
    resolved: int
    open: int
    median_resolution_hours: float | None


@router.get("/outcome-summary", response_model=OutcomeSummaryResponse)
def get_outcome_summary(
    days: int = Query(90, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> OutcomeSummaryResponse:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(
        McpScoreDispute.id,
        McpScoreDispute.server_id,
        McpScoreDispute.proposed_overall_risk,
        McpScoreDispute.reason_category,
        McpScoreDispute.status,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).where(McpScoreDispute.created_at >= since)

    rows = db.execute(statement).all()
    summary = summarize_dispute_outcomes(rows, days)
    return OutcomeSummaryResponse(**summary)