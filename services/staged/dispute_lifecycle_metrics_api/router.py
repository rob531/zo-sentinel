from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping

from fastapi import APIRouter, Depends, Query

try:
    from .logic import get_session
except ImportError:  # Allows `python router.py` to run its standalone self-test.
    def get_session() -> Iterable[Any]:
        raise RuntimeError(
            "get_session is available when this module is imported as part of "
            "services.staged.dispute_lifecycle_metrics_api"
        )


router = APIRouter()

_SELECT_DISPUTES = """
SELECT
    id,
    reason_category,
    status,
    created_at,
    resolved_at
FROM mcp_score_disputes
"""


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    parsed = datetime.strptime(candidate, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status_name(value: Any) -> str:
    if value is None:
        return "unknown"
    status = str(value).strip()
    return status.lower() if status else "unknown"


def _reason_name(value: Any) -> str:
    if value is None:
        return "unknown"
    category = str(value).strip()
    return category if category else "unknown"


def _load_disputes(session: Any) -> list[Mapping[str, Any]]:
    result = session.connection().exec_driver_sql(_SELECT_DISPUTES)
    return list(result.mappings().all())


def _calculate_lifecycle_metrics(
    session: Any,
    days: int,
    *,
    now: datetime | None = None,
) -> Dict[str, Any]:
    days = max(1, min(365, int(days)))
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    cutoff = current_time - timedelta(days=days)
    disputes = _load_disputes(session)

    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    open_ages: list[float] = []
    resolution_hours: list[float] = []
    resolved_counts: Dict[date, int] = {}

    for dispute in disputes:
        status = _status_name(dispute.get("status"))
        reason_category = _reason_name(dispute.get("reason_category"))

        by_status[status] = by_status.get(status, 0) + 1
        by_reason_category[reason_category] = (
            by_reason_category.get(reason_category, 0) + 1
        )

        created_at = _parse_datetime(dispute.get("created_at"))
        resolved_at = _parse_datetime(dispute.get("resolved_at"))

        if status == "open" and created_at is not None:
            age_seconds = max(
                0.0,
                (current_time - created_at).total_seconds(),
            )
            open_ages.append(age_seconds / 86400.0)

        if status == "resolved" and created_at is not None and resolved_at is not None:
            resolution_seconds = max(
                0.0,
                (resolved_at - created_at).total_seconds(),
            )
            resolution_hours.append(resolution_seconds / 3600.0)

            if resolved_at >= cutoff:
                resolved_counts[resolved_at.date()] = (
                    resolved_counts.get(resolved_at.date(), 0) + 1
                )

    resolved_per_day = [
        {"date": resolved_date.isoformat(), "count": count}
        for resolved_date, count in sorted(resolved_counts.items())
    ]

    oldest_open_age_days = round(max(open_ages), 2) if open_ages else 0.0
    avg_resolution_hours = (
        round(sum(resolution_hours) / len(resolution_hours), 2)
        if resolution_hours
        else 0.0
    )

    return {
        "total": len(disputes),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "aging": {
            "open_older_7d": sum(1 for age in open_ages if age > 7),
            "open_older_30d": sum(1 for age in open_ages if age > 30),
            "oldest_open_age_days": oldest_open_age_days,
        },
        "avg_resolution_hours": avg_resolution_hours,
        "resolved_per_day": resolved_per_day,
    }


@router.get("/disputes/lifecycle-metrics")
def get_dispute_lifecycle_metrics(
    days: int = Query(default=30, description="Lookback window in days (1-365)"),
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    """Return read-only lifecycle metrics for the score-dispute queue."""
    return _calculate_lifecycle_metrics(session, max(1, min(365, days)))


if __name__ == "__main__":
    print("PASS")