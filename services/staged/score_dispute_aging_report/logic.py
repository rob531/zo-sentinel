"""Logic for the score dispute aging report."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_TERMINAL_STATUSES = {"resolved", "rejected"}
_AGE_BUCKETS = ("0-2d", "3-7d", "8-30d", "31-90d", "90d+")


def _row_value(row: Mapping[str, Any] | Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _age_bucket(age_days: float) -> str:
    if age_days <= 2:
        return "0-2d"
    if age_days <= 7:
        return "3-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "90d+"


def aging_report(rows: list[dict], now: datetime) -> dict:
    """Build an aging report from dispute rows.

    Only disputes whose status is not ``resolved`` or ``rejected`` are treated
    as open.  Age is calculated in Python so the function has no database or
    database-specific SQL dependency.
    """
    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {bucket: 0 for bucket in _AGE_BUCKETS}
    total_open = 0
    total_resolved = 0
    oldest_open_days = 0.0

    for row in rows:
        status_value = _row_value(row, "status", "") or ""
        status = str(status_value)
        status_key = status.strip() or "unknown"

        if status.strip().lower() in _TERMINAL_STATUSES:
            total_resolved += 1
            continue

        total_open += 1
        by_status[status_key] = by_status.get(status_key, 0) + 1

        reason_value = _row_value(row, "reason_category", "") or ""
        reason_key = str(reason_value).strip() or "unknown"
        by_reason_category[reason_key] = by_reason_category.get(reason_key, 0) + 1

        created_at = _row_value(row, "created_at")
        if isinstance(created_at, datetime):
            age_days = max(
                0.0,
                (_naive_utc(now) - _naive_utc(created_at)).total_seconds() / 86400.0,
            )
            bucket_counts[_age_bucket(age_days)] += 1
            oldest_open_days = max(oldest_open_days, age_days)

    return {
        "total_open": total_open,
        "total_resolved": total_resolved,
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "age_buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in _AGE_BUCKETS
        ],
        "oldest_open_days": float(oldest_open_days),
        "generated_at": now.isoformat(),
    }


def load_dispute_rows(db: Session = Depends(get_session)) -> List[dict]:
    """Load dispute data through the application's SQLAlchemy session."""
    result = db.execute(
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.admin_note,
        )
    )
    return [dict(row) for row in result.mappings().all()]


def build_aging_report(
    db: Session = Depends(get_session), now: datetime | None = None
) -> dict:
    """Load disputes and return the report for the current time."""
    current_time = now or datetime.now(timezone.utc)
    return aging_report(load_dispute_rows(db), current_time)


if __name__ == "__main__":
    from datetime import timedelta

    test_now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    test_rows = [
        {
            "status": "open",
            "reason_category": "scoring",
            "created_at": test_now - timedelta(days=1),
        },
        {
            "status": "open",
            "reason_category": "scoring",
            "created_at": test_now - timedelta(days=10),
        },
        {
            "status": "open",
            "reason_category": "policy",
            "created_at": test_now - timedelta(days=200),
        },
        {
            "status": "resolved",
            "reason_category": "scoring",
            "created_at": test_now - timedelta(days=30),
            "resolved_at": test_now,
        },
    ]
    report = aging_report(test_rows, test_now)
    assert report["total_open"] == 3
    assert next(
        bucket for bucket in report["age_buckets"] if bucket["bucket"] == "90d+"
    )["count"] == 1
    assert report["oldest_open_days"] >= 200
    assert report["total_resolved"] == 1
    print("PASS")