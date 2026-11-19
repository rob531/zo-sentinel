"""Data and computation layer for the score dispute ledger."""
from __future__ import annotations

from typing import Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


VERDICT_TIER_RANK: Dict[str, int] = {
    "TRUSTED_GENERAL": 6,
    "TRUSTED_SPECIALIST": 5,
    "NEUTRAL": 4,
    "ELEVATED_RISK": 3,
    "HIGH_RISK": 2,
    "HIGH_RISK_ISOLATED": 1,
}


def _tier_rank(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    return VERDICT_TIER_RANK.get(str(value).strip().upper())


def _tier_delta(proposed: Optional[str], current: Optional[str]) -> str:
    proposed_rank = _tier_rank(proposed)
    current_rank = _tier_rank(current)
    if proposed_rank is None or current_rank is None:
        return "unknown"
    if proposed_rank > current_rank:
        return "up"
    if proposed_rank < current_rank:
        return "down"
    return "same"


def _iso8601(value) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def fetch_dispute_ledger(
    conn: Session,
    status: str | None = None,
    server_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Return a paginated ledger of score disputes and registry tier changes."""
    filters = []
    if status is not None:
        filters.append(McpScoreDispute.status == status)
    if server_id is not None:
        filters.append(McpScoreDispute.server_id == server_id)

    total_stmt = select(func.count()).select_from(McpScoreDispute)
    if filters:
        total_stmt = total_stmt.where(*filters)
    total = conn.execute(total_stmt).scalar_one()

    stmt = (
        select(
            McpScoreDispute,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
    )
    if filters:
        stmt = stmt.where(*filters)

    stmt = (
        stmt.order_by(McpScoreDispute.created_at.desc())
        .limit(max(0, int(limit)))
        .offset(max(0, int(offset)))
    )

    items = []
    for row in conn.execute(stmt).all():
        dispute = row[0]
        registry_name = row[1]
        current_risk_tier = row[2]
        items.append(
            {
                "id": dispute.id,
                "server_id": dispute.server_id,
                "name": registry_name,
                "current_risk_tier": current_risk_tier,
                "submitted_by": dispute.submitted_by,
                "proposed_overall_risk": dispute.proposed_overall_risk,
                "reason_category": dispute.reason_category,
                "status": dispute.status,
                "explanation": dispute.explanation,
                "admin_note": dispute.admin_note,
                "created_at": _iso8601(dispute.created_at),
                "resolved_at": _iso8601(dispute.resolved_at),
                "tier_delta": _tier_delta(
                    dispute.proposed_overall_risk,
                    current_risk_tier,
                ),
            }
        )

    return {"total": int(total), "items": items}