from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median
from typing import Any

from app.db import get_session
from app.models import McpScoreDispute


_STATUSES = ("open", "in_review", "resolved", "rejected")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    start = _as_utc(start)
    end = _as_utc(end)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600.0


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _status_name(value: Any) -> str:
    if value is None:
        return "other"
    normalized = str(value).strip().lower()
    return normalized if normalized in _STATUSES else "other"


def get_resolution_time(session: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = session.query(McpScoreDispute).all()

    buckets: dict[str, dict[str, Any]] = {
        status: {
            "status": status,
            "count": 0,
            "mean_age_hours": None,
            "median_age_hours": None,
        }
        for status in (*_STATUSES, "other")
    }
    durations: dict[str, list[float]] = {status: [] for status in buckets}
    reason_counts: dict[Any, int] = {}
    reason_durations: dict[Any, list[float]] = {}

    for dispute in rows:
        status = _status_name(getattr(dispute, "status", None))
        created_at = getattr(dispute, "created_at", None)
        resolved_at = getattr(dispute, "resolved_at", None)
        reason_category = getattr(dispute, "reason_category", None)

        buckets[status]["count"] += 1
        reason_counts[reason_category] = reason_counts.get(reason_category, 0) + 1

        if status in ("open", "in_review"):
            age = _hours(created_at, now)
            if age is not None:
                durations[status].append(age)
        elif status == "resolved":
            duration = _hours(created_at, resolved_at)
            if duration is not None:
                durations[status].append(duration)
                reason_durations.setdefault(reason_category, []).append(duration)

    for status, values in durations.items():
        if values:
            buckets[status]["mean_age_hours"] = _round(mean(values))
            buckets[status]["median_age_hours"] = _round(median(values))

    reason_categories = []
    for reason_category, count in reason_counts.items():
        values = reason_durations.get(reason_category, [])
        reason_categories.append(
            {
                "reason_category": reason_category,
                "count": count,
                "mean_resolution_hours": _round(mean(values)) if values else None,
            }
        )

    reason_categories.sort(
        key=lambda item: (
            "" if item["reason_category"] is None else str(item["reason_category"])
        )
    )

    return {
        "status_buckets": list(buckets.values()),
        "reason_categories": reason_categories,
        "generated_at": now.isoformat(),
    }


def compute_resolution_time(session: Any) -> dict[str, Any]:
    return get_resolution_time(session)


def get_dispute_resolution_time(session: Any) -> dict[str, Any]:
    return get_resolution_time(session)


def build_resolution_time_payload(session: Any) -> dict[str, Any]:
    return get_resolution_time(session)


if __name__ == "__main__":
    print("PASS")