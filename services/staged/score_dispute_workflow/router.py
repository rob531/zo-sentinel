from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

try:
    from .logic import get_score_disputes as _logic_get_score_disputes
except ImportError:
    _logic_get_score_disputes = None

router = APIRouter(prefix="/api")

_TIER_ORDER = {
    "UNKNOWN": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


class DisputeWorkflowItem(BaseModel):
    id: int | str
    server_id: str
    server_name: str | None = None
    current_tier: str
    proposed_tier: str
    proposed_overall_risk: float | None = None
    tier_delta: int | None = None
    direction: str
    reason_category: str | None = None
    days_open: int
    created_at: datetime
    resolved_at: datetime | None = None


class DisputeWorkflowResponse(BaseModel):
    status_filter: str
    count: int
    disputes: list[DisputeWorkflowItem]


def _normalize_tier(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    tier = str(value).strip().upper()
    return tier if tier in _TIER_ORDER else "UNKNOWN"


def _numeric_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _proposed_tier(proposed_risk: Any, latest_p_top: Any = None) -> str:
    if isinstance(proposed_risk, str):
        proposed = _normalize_tier(proposed_risk)
        if proposed != "UNKNOWN":
            return proposed

    score = _numeric_score(proposed_risk)
    if score is None:
        score = _numeric_score(latest_p_top)
    if score is None:
        return "UNKNOWN"
    if score < 0.4:
        return "LOW"
    if score < 0.7:
        return "MEDIUM"
    if score < 0.9:
        return "HIGH"
    return "CRITICAL"


def _tier_comparison(current_tier: str, proposed_tier: str) -> tuple[int | None, str]:
    current_order = _TIER_ORDER.get(current_tier)
    proposed_order = _TIER_ORDER.get(proposed_tier)
    if not current_order or not proposed_order:
        return None, "unknown"
    delta = abs(proposed_order - current_order)
    if proposed_order > current_order:
        return delta, "escalation"
    if proposed_order < current_order:
        return delta, "downgrade"
    return delta, "unchanged"


def _age_seconds(created_at: datetime) -> float:
    if created_at.tzinfo is None:
        now = datetime.now()
    else:
        now = datetime.now(timezone.utc)
    return max(0.0, (now - created_at).total_seconds())


@router.get(
    "/disputes/workflow",
    response_model=DisputeWorkflowResponse,
    name="get_score_dispute_workflow",
)
def get_score_dispute_workflow(
    status: str = Query("open"),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> DisputeWorkflowResponse:
    latest_score = (
        select(McpLlmAxisScore.p_top)
        .where(
            McpLlmAxisScore.server_id == McpScoreDispute.server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
        )
        .order_by(
            desc(McpLlmAxisScore.scored_at),
            desc(McpLlmAxisScore.id),
        )
        .limit(1)
        .correlate(McpScoreDispute)
        .scalar_subquery()
    )

    statement = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpServerRegistry.name.label("server_name"),
            McpServerRegistry.risk_tier,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            latest_score.label("latest_p_top"),
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
    )

    normalized_status = status.strip()
    if normalized_status.lower() not in {"", "all", "any"}:
        statement = statement.where(McpScoreDispute.status == normalized_status)
    statement = statement.limit(limit)

    rows = session.execute(statement).all()
    disputes: list[DisputeWorkflowItem] = []

    for row in rows:
        current_tier = _normalize_tier(row.risk_tier)
        proposed_tier = _proposed_tier(
            row.proposed_overall_risk,
            row.latest_p_top,
        )
        tier_delta, direction = _tier_comparison(current_tier, proposed_tier)
        age_seconds = _age_seconds(row.created_at)

        disputes.append(
            DisputeWorkflowItem(
                id=row.id,
                server_id=row.server_id,
                server_name=row.server_name,
                current_tier=current_tier,
                proposed_tier=proposed_tier,
                proposed_overall_risk=_numeric_score(row.proposed_overall_risk),
                tier_delta=tier_delta,
                direction=direction,
                reason_category=row.reason_category,
                days_open=int(age_seconds // 86400),
                created_at=row.created_at,
                resolved_at=row.resolved_at,
            )
        )

    disputes.sort(key=lambda item: item.days_open, reverse=True)

    return DisputeWorkflowResponse(
        status_filter=normalized_status,
        count=len(disputes),
        disputes=disputes,
    )