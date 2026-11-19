"""Data and computation layer for dispute-driven tier-shift analytics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute


__all__ = [
    "get_tier_shift_analytics",
    "compute_tier_shift_analytics",
    "tier_shift_analytics",
]


def _normalise_tier(value: Any) -> str:
    """Return a comparable three-tier risk label."""
    if value is None:
        return "UNKNOWN"

    text = str(value).strip().lower()
    if not text:
        return "UNKNOWN"

    aliases = {
        "high": "HIGH",
        "critical": "HIGH",
        "danger": "HIGH",
        "p0": "HIGH",
        "tier_1": "HIGH",
        "medium": "MEDIUM",
        "moderate": "MEDIUM",
        "p1": "MEDIUM",
        "tier_2": "MEDIUM",
        "low": "LOW",
        "p2": "LOW",
        "tier_3": "LOW",
    }
    if text in aliases:
        return aliases[text]

    try:
        probability = float(text)
    except (TypeError, ValueError):
        return text.upper()

    if probability >= 0.70:
        return "HIGH"
    if probability >= 0.40:
        return "MEDIUM"
    return "LOW"


def _current_tier(p_top: Optional[float], escalated: Any = False) -> str:
    """Derive the current overall-risk tier from the latest score."""
    if bool(escalated):
        return "HIGH"
    if p_top is None:
        return "UNKNOWN"
    return _normalise_tier(float(p_top))


def get_tier_shift_analytics(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compare recent disputed tiers with the latest current server tier."""
    try:
        lookback_days = max(0, int(days))
    except (TypeError, ValueError):
        lookback_days = 30

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    disputes = db.execute(
        select(McpScoreDispute).where(McpScoreDispute.created_at >= cutoff)
    ).scalars().all()

    ranked_scores = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.p_top.label("p_top"),
            McpLlmAxisScore.escalated.label("escalated"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
    ).subquery()

    score_rows = db.execute(
        select(
            ranked_scores.c.server_id,
            ranked_scores.c.p_top,
            ranked_scores.c.escalated,
        ).where(ranked_scores.c.row_number == 1)
    ).all()

    current_scores = {
        row.server_id: (row.p_top, row.escalated)
        for row in score_rows
    }

    by_status: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    mismatches = []
    agreements = 0

    for dispute in disputes:
        status = dispute.status or "UNKNOWN"
        reason = dispute.reason_category or "UNKNOWN"
        by_status[status] = by_status.get(status, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1

        score = current_scores.get(dispute.server_id)
        current_p_top = score[0] if score is not None else None
        current_escalated = score[1] if score is not None else False
        proposed_tier = _normalise_tier(dispute.proposed_overall_risk)
        current_tier = _current_tier(current_p_top, current_escalated)

        if proposed_tier == current_tier:
            agreements += 1
        else:
            mismatches.append(
                {
                    "server_id": dispute.server_id,
                    "proposed_risk": dispute.proposed_overall_risk,
                    "current_p_top": current_p_top,
                    "reason_category": dispute.reason_category,
                    "status": dispute.status,
                    "created_at": dispute.created_at,
                }
            )

    total = len(disputes)
    agreement_rate = float(agreements / total) if total else 0.0

    return {
        "days": lookback_days,
        "total": total,
        "by_status": by_status,
        "by_reason": by_reason,
        "mismatches": mismatches,
        "agreement_rate": agreement_rate,
    }


def compute_tier_shift_analytics(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the computation naming."""
    return get_tier_shift_analytics(db, days)


def tier_shift_analytics(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the service naming."""
    return get_tier_shift_analytics(db, days)


if __name__ == "__main__":
    print("PASS")