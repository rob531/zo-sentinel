from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


router = APIRouter()


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _utc_datetime(value).isoformat()


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def get_resolution_cycle(db: Session, days: int) -> Dict:
    """Build the dispute resolution-cycle report for the requested lookback."""
    if days < 0:
        raise ValueError("days must be zero or greater")

    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at.timestamp() - (days * 86400)

    rows = db.execute(
        select(
            McpScoreDispute.server_id,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
    ).all()

    disputes = []
    for server_id, status, reason_category, created_at, resolved_at in rows:
        if created_at is None:
            continue
        created_utc = _utc_datetime(created_at)
        if created_utc.timestamp() < cutoff:
            continue
        resolved_utc = (
            _utc_datetime(resolved_at) if resolved_at is not None else None
        )
        status_value = status or "unknown"
        is_resolved = (
            status_value.lower() == "resolved" or resolved_utc is not None
        )
        disputes.append(
            {
                "server_id": server_id,
                "status": status_value,
                "reason_category": reason_category or "unknown",
                "created_at": created_utc,
                "resolved_at": resolved_utc,
                "is_resolved": is_resolved,
            }
        )

    submitted = len(disputes)
    resolved_disputes = [item for item in disputes if item["is_resolved"]]
    open_disputes = [item for item in disputes if not item["is_resolved"]]

    resolution_hours = []
    for item in resolved_disputes:
        if item["resolved_at"] is None:
            continue
        elapsed = item["resolved_at"] - item["created_at"]
        resolution_hours.append(max(0.0, elapsed.total_seconds() / 3600.0))

    status_counts: Dict[str, int] = {}
    status_oldest: Dict[str, datetime] = {}
    reason_counts: Dict[str, int] = {}

    for item in disputes:
        status = item["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        created_at = item["created_at"]
        if status not in status_oldest or created_at < status_oldest[status]:
            status_oldest[status] = created_at

        reason = item["reason_category"]
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    by_status = [
        {
            "status": status,
            "count": count,
            "oldest_created_at": _isoformat(status_oldest[status]),
        }
        for status, count in sorted(
            status_counts.items(), key=lambda entry: (-entry[1], entry[0])
        )
    ]

    by_reason_category = [
        {"reason_category": reason, "count": count}
        for reason, count in sorted(
            reason_counts.items(), key=lambda entry: (-entry[1], entry[0])
        )
    ]

    aging_open = []
    for item in open_disputes:
        age_days = max(
            0.0,
            (generated_at - item["created_at"]).total_seconds() / 86400.0,
        )
        aging_open.append(
            {
                "server_id": item["server_id"],
                "status": item["status"],
                "created_at": _isoformat(item["created_at"]),
                "age_days": age_days,
            }
        )

    aging_open.sort(key=lambda item: (-item["age_days"], item["server_id"] or ""))
    aging_open = aging_open[:50]

    return {
        "days": days,
        "generated_at": generated_at.isoformat(),
        "totals": {
            "submitted": submitted,
            "resolved": len(resolved_disputes),
            "open": len(open_disputes),
            "median_resolution_hours": (
                float(median(resolution_hours)) if resolution_hours else None
            ),
            "p90_resolution_hours": _percentile(resolution_hours, 0.9),
        },
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "aging_open": aging_open,
    }


@router.get("/disputes/resolution-cycle")
def resolution_cycle_endpoint(
    days: int = Query(..., ge=0),
    db: Session = Depends(get_session),
) -> Dict:
    return get_resolution_cycle(db, days)