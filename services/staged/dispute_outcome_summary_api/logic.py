"""logic.py -- dispute outcome summary data and computation layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, select
from sqlalchemy.orm import Session

from app.models import McpScoreDispute


def _resolution_hours(created_at: datetime, resolved_at: datetime) -> float:
    """Return the elapsed resolution time in hours."""
    if created_at.tzinfo is not None and resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=timezone.utc)
    elif created_at.tzinfo is None and resolved_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (resolved_at - created_at).total_seconds() / 3600.0


def summarize_disputes(db: Session, days: int = 90) -> Dict[str, Any]:
    """Summarise disputes created during the requested rolling window."""
    if days < 1:
        raise ValueError("days must be at least 1")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    statement = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
        .where(McpScoreDispute.created_at >= bindparam("since"))
        .params(since=since)
    )
    rows = db.execute(statement).all()

    by_status: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    by_proposed_risk: Dict[str, int] = {}
    resolution_hours: List[float] = []

    for row in rows:
        status = row.status or "unknown"
        reason = row.reason_category or "unknown"
        proposed_risk = row.proposed_overall_risk or "unknown"

        by_status[status] = by_status.get(status, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_proposed_risk[proposed_risk] = by_proposed_risk.get(proposed_risk, 0) + 1

        if status == "resolved" and row.resolved_at is not None:
            resolution_hours.append(
                _resolution_hours(row.created_at, row.resolved_at)
            )

    resolution_hours.sort()
    median_resolution_hours: Optional[float]
    if not resolution_hours:
        median_resolution_hours = None
    else:
        midpoint = len(resolution_hours) // 2
        if len(resolution_hours) % 2:
            median_resolution_hours = resolution_hours[midpoint]
        else:
            median_resolution_hours = (
                resolution_hours[midpoint - 1] + resolution_hours[midpoint]
            ) / 2.0

    return {
        "days": days,
        "total": len(rows),
        "by_status": by_status,
        "by_reason": [
            {"reason_category": reason, "count": by_reason[reason]}
            for reason in sorted(by_reason)
        ],
        "by_proposed_risk": by_proposed_risk,
        "resolved": by_status.get("resolved", 0),
        "open": by_status.get("open", 0),
        "median_resolution_hours": median_resolution_hours,
    }


get_dispute_outcome_summary = summarize_disputes