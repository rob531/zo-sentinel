from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpScoreDispute as ScoreDispute  # type: ignore
except ImportError:
    try:
        from app.models import McpScoreDispute as ScoreDispute  # type: ignore
    except ImportError:
        from app.models import ScoreDispute  # type: ignore


BUCKET_LABELS = ("0-2d", "3-7d", "8-30d", "31-90d", "90d+")


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    """Convert values returned by PostgreSQL or SQLite into UTC datetimes."""
    if value is None:
        return None

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _age_in_days(created_at: Any, now: datetime) -> Optional[int]:
    normalized = _as_utc_datetime(created_at)
    if normalized is None:
        return None

    age_seconds = (now - normalized).total_seconds()
    if age_seconds < 0:
        return 0
    return int(age_seconds // 86400)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 2:
        return "0-2d"
    if age_days <= 7:
        return "3-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "90d+"


def _row_value(row: Any, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, TypeError, IndexError):
        return getattr(row, name, None)


def build_open_age_buckets(
    session: Session,
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build open-dispute age buckets from the application database.

    The query only reads rows from the real application table. Age assignment
    and the median are deliberately calculated in Python so that the result is
    consistent across PostgreSQL and the contract's SQLite database.
    """
    if days is None:
        days = 30
    days = int(days)
    if days < 0:
        raise ValueError("days must be non-negative")

    reference_time = now or datetime.now(timezone.utc)
    reference_time = _as_utc_datetime(reference_time) or datetime.now(timezone.utc)

    statement = text(
        """
        SELECT id, created_at
        FROM mcp_score_disputes
        WHERE status = :status
          AND resolved_at IS NULL
        """
    )
    rows = session.execute(statement, {"status": "open"}).fetchall()

    bucket_counts: Dict[str, int] = {label: 0 for label in BUCKET_LABELS}
    bucket_oldest: Dict[str, Optional[Any]] = {label: None for label in BUCKET_LABELS}
    bucket_oldest_normalized: Dict[str, Optional[datetime]] = {
        label: None for label in BUCKET_LABELS
    }
    ages: List[int] = []

    for row in rows:
        created_at = _row_value(row, "created_at")
        age_days = _age_in_days(created_at, reference_time)
        if age_days is None:
            continue

        ages.append(age_days)
        label = _bucket_for_age(age_days)
        bucket_counts[label] += 1

        normalized_created_at = _as_utc_datetime(created_at)
        previous_normalized = bucket_oldest_normalized[label]
        if (
            normalized_created_at is not None
            and (
                previous_normalized is None
                or normalized_created_at < previous_normalized
            )
        ):
            bucket_oldest_normalized[label] = normalized_created_at
            bucket_oldest[label] = created_at

    buckets = [
        {
            "bucket": label,
            "count": bucket_counts[label],
            "oldest_created_at": bucket_oldest[label],
        }
        for label in BUCKET_LABELS
    ]

    median_age_days: Optional[float] = None
    if ages:
        median_age_days = float(median(ages))

    return {
        "days": days,
        "total_open": len(ages),
        "buckets": buckets,
        "median_age_days": median_age_days,
    }


def get_open_age_buckets(
    session: Session,
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    return build_open_age_buckets(session=session, days=days, now=now)


def compute_open_age_buckets(
    session: Session,
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    return build_open_age_buckets(session=session, days=days, now=now)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SqlAlchemySession

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    submitted_by INTEGER,
                    proposed_overall_risk REAL,
                    reason_category TEXT,
                    status TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            (1, "open", now - timedelta(days=0), None),
            (2, "open", now - timedelta(days=2), None),
            (3, "open", now - timedelta(days=7), None),
            (4, "open", now - timedelta(days=100), None),
            (5, "resolved", now - timedelta(days=1), now),
        ]
        for row_id, status, created_at, resolved_at in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_score_disputes
                        (id, server_id, submitted_by, proposed_overall_risk,
                         reason_category, status, created_at, resolved_at)
                    VALUES
                        (:id, 1, 1, 0.5, 'test', :status, :created_at, :resolved_at)
                    """
                ),
                {
                    "id": row_id,
                    "status": status,
                    "created_at": created_at.replace(tzinfo=None),
                    "resolved_at": (
                        resolved_at.replace(tzinfo=None)
                        if resolved_at is not None
                        else None
                    ),
                },
            )

    with SqlAlchemySession(engine) as session:
        result = build_open_age_buckets(session=session, days=365, now=now)
        assert result["total_open"] == 4
        assert sum(item["count"] for item in result["buckets"]) == 4
        assert all(item["count"] > 0 for item in result["buckets"] if item["bucket"] == "90d+")
        assert result["median_age_days"] is not None

    print("PASS")