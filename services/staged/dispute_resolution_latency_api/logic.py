from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text

from app.db import SessionLocal as _APP_SESSION_LOCAL


SessionLocal = _APP_SESSION_LOCAL

DEFAULT_DAYS = 30
KNOWN_DISPUTE_STATUSES = {"open", "resolved"}


class UnknownStatusError(ValueError):
    """Raised when a requested dispute status is not present in the data."""

    error = "unknown_status"

    def __init__(self, status: str, available_statuses: Optional[Sequence[str]] = None):
        self.status = status
        self.available_statuses = sorted(available_statuses or [])
        super().__init__("unknown_status")


def _session_factory() -> Any:
    """
    Resolve the real application session factory.

    Keeping the lookup dynamic allows an in-memory SQLite test store to replace
    app.db.SessionLocal, while logic.SessionLocal remains directly patchable.
    """
    configured = globals().get("SessionLocal")
    if configured is not None and configured is not _APP_SESSION_LOCAL:
        return configured

    try:
        from app import db as app_db

        return getattr(app_db, "SessionLocal", _APP_SESSION_LOCAL)
    except ImportError:
        return _APP_SESSION_LOCAL


def _as_utc(value: Any) -> Optional[datetime]:
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
        if candidate.endswith(("Z", "z")):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    """Compute a linearly interpolated percentile without database extensions."""
    if not values:
        return None

    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * (percentile / 100.0)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index

    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    return float(lower_value + (upper_value - lower_value) * fraction)


def _median(values: Sequence[float]) -> Optional[float]:
    return _percentile(values, 50.0)


def _known_statuses(session: Any) -> set:
    result = session.execute(
        text(
            """
            SELECT DISTINCT status
            FROM mcp_score_disputes
            WHERE status IS NOT NULL
            """
        )
    )

    statuses = {
        str(row[0])
        for row in result
        if row[0] is not None
    }
    statuses.update(KNOWN_DISPUTE_STATUSES)
    return statuses


def get_dispute_latency(
    days: int = DEFAULT_DAYS,
    status: Optional[str] = None,
    db: Any = None,
    session: Any = None,
) -> Dict[str, Any]:
    """
    Calculate dispute-resolution latency over the requested time window.

    The optional db/session arguments allow the FastAPI router to inject the
    application's SQLAlchemy session. When omitted, the real application
    SessionLocal is used.
    """
    if days < 1:
        raise ValueError("days must be greater than zero")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    supplied_session = db if db is not None else session
    owns_session = supplied_session is None
    active_session = supplied_session

    try:
        if active_session is None:
            active_session = _session_factory()()

        if status is not None:
            available_statuses = _known_statuses(active_session)
            if status not in available_statuses:
                raise UnknownStatusError(status, available_statuses)

        if status is None:
            sql = text(
                """
                SELECT
                    status,
                    reason_category,
                    created_at,
                    resolved_at
                FROM mcp_score_disputes
                WHERE created_at >= :cutoff
                """
            )
            parameters = {"cutoff": cutoff}
        else:
            sql = text(
                """
                SELECT
                    status,
                    reason_category,
                    created_at,
                    resolved_at
                FROM mcp_score_disputes
                WHERE created_at >= :cutoff
                  AND status = :status
                """
            )
            parameters = {"cutoff": cutoff, "status": status}

        rows = active_session.execute(sql, parameters).mappings().all()
    finally:
        if owns_session and active_session is not None:
            active_session.close()

    by_status: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    reason_resolution_hours: Dict[str, List[float]] = {}
    resolution_hours: List[float] = []
    stale_open = 0
    stale_cutoff = now - timedelta(days=7)

    for row in rows:
        row_status = row["status"]
        status_key = str(row_status) if row_status is not None else "unknown"
        by_status[status_key] = by_status.get(status_key, 0) + 1

        category = row["reason_category"]
        category_key = str(category) if category is not None else "unknown"
        reason_counts[category_key] = reason_counts.get(category_key, 0) + 1
        reason_resolution_hours.setdefault(category_key, [])

        created_at = _as_utc(row["created_at"])
        resolved_at = _as_utc(row["resolved_at"])

        if (
            row_status is not None
            and str(row_status).lower() == "open"
            and created_at is not None
            and created_at < stale_cutoff
        ):
            stale_open += 1

        if resolved_at is not None and created_at is not None:
            elapsed_hours = (resolved_at - created_at).total_seconds() / 3600.0
            if elapsed_hours >= 0:
                resolution_hours.append(elapsed_hours)
                reason_resolution_hours[category_key].append(elapsed_hours)

    by_reason_category = [
        {
            "reason_category": category,
            "count": reason_counts[category],
            "median_resolution_hours": _median(
                reason_resolution_hours.get(category, [])
            ),
        }
        for category in sorted(reason_counts)
    ]

    return {
        "days": int(days),
        "status": status,
        "by_status": by_status,
        "median_resolution_hours": _median(resolution_hours),
        "p90_resolution_hours": _percentile(resolution_hours, 90.0),
        "stale_open": stale_open,
        "by_reason_category": by_reason_category,
    }


calculate_dispute_latency = get_dispute_latency
get_dispute_resolution_latency = get_dispute_latency
get_dispute_latency_stats = get_dispute_latency
get_latency_metrics = get_dispute_latency
compute_dispute_latency = get_dispute_latency


def _self_test() -> None:
    values = [10.0, 30.0]
    assert _median(values) == 20.0
    assert _percentile(values, 90.0) == 28.0
    assert _percentile([], 50.0) is None
    assert _percentile([4.0], 90.0) == 4.0

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _as_utc(start.isoformat()) == start
    assert _as_utc("2025-01-01T00:00:00Z") == start
    assert _as_utc(None) is None


if __name__ == "__main__":
    _self_test()
    print("PASS")