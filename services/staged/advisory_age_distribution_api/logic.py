from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import VulnAdvisory
except ImportError:
    try:
        from app.models import VulnerabilityAdvisory as VulnAdvisory
    except ImportError:
        from app.models import Advisory as VulnAdvisory


DEFAULT_DAYS = 365
MAX_DAYS = 3650

BUCKET_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"label": "0-7", "min_days": 0, "max_days": 7},
    {"label": "8-30", "min_days": 8, "max_days": 30},
    {"label": "31-90", "min_days": 31, "max_days": 90},
    {"label": "91-180", "min_days": 91, "max_days": 180},
    {"label": "180+", "min_days": 181, "max_days": None},
)


def _utc_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime without changing the instant."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_as_of(value: datetime | str | None) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        value = datetime.fromisoformat(normalized)
    return _utc_datetime(value)


def _age_in_days(published_at: datetime, as_of: datetime) -> int:
    elapsed = (_utc_datetime(as_of) - _utc_datetime(published_at)).total_seconds()
    return max(0, int(elapsed // 86400))


def _bucket_index(age_days: int) -> int:
    if age_days <= 7:
        return 0
    if age_days <= 30:
        return 1
    if age_days <= 90:
        return 2
    if age_days <= 180:
        return 3
    return 4


def _empty_bucket_counts() -> list[int]:
    return [0 for _ in BUCKET_DEFINITIONS]


def _build_distribution(
    rows: Iterable[tuple[Any, datetime]],
    *,
    days: int,
    as_of: datetime,
) -> dict[str, Any]:
    overall_counts = _empty_bucket_counts()
    severity_counts: dict[str, list[int]] = {}

    for severity, published_at in rows:
        if published_at is None:
            continue

        age_days = _age_in_days(published_at, as_of)
        index = _bucket_index(age_days)
        overall_counts[index] += 1

        severity_name = str(severity).strip() if severity is not None else "unknown"
        if not severity_name:
            severity_name = "unknown"
        severity_counts.setdefault(severity_name, _empty_bucket_counts())[index] += 1

    buckets = [
        {
            "label": definition["label"],
            "min_days": definition["min_days"],
            "max_days": definition["max_days"],
            "count": overall_counts[index],
        }
        for index, definition in enumerate(BUCKET_DEFINITIONS)
    ]

    by_severity = {
        severity: [
            {"label": definition["label"], "count": severity_counts[severity][index]}
            for index, definition in enumerate(BUCKET_DEFINITIONS)
        ]
        for severity in sorted(severity_counts)
    }

    return {
        "days": days,
        "as_of": as_of,
        "buckets": buckets,
        "by_severity": by_severity,
        "total": sum(overall_counts),
    }


def get_age_distribution(
    days: int = DEFAULT_DAYS,
    session: Session = Depends(get_session),
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Compute advisory age bands within the requested publication window.

    The snapshot time is the newest ``fetched_at`` value when one is available,
    making the result deterministic for a given database snapshot. A caller may
    supply an explicit ``as_of`` value for reproducible analysis.
    """
    if isinstance(days, bool) or not isinstance(days, int):
        raise ValueError("days must be an integer")
    if days < 1 or days > MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")

    requested_as_of = _parse_as_of(as_of)

    if requested_as_of is None:
        max_fetched = session.execute(
            select(func.max(VulnAdvisory.fetched_at))
        ).scalar()
        requested_as_of = (
            _utc_datetime(max_fetched)
            if max_fetched is not None
            else datetime.now(timezone.utc)
        )

    cutoff = requested_as_of - timedelta(days=days)

    statement = (
        select(VulnAdvisory.severity, VulnAdvisory.published_at)
        .where(
            VulnAdvisory.published_at.is_not(None),
            VulnAdvisory.published_at >= cutoff,
            VulnAdvisory.published_at <= requested_as_of,
        )
        .order_by(VulnAdvisory.published_at, VulnAdvisory.id)
    )

    rows = [
        (severity, _utc_datetime(published_at))
        for severity, published_at in session.execute(statement).all()
        if published_at is not None
    ]

    return _build_distribution(rows, days=days, as_of=requested_as_of)


def get_advisory_age_distribution(
    days: int = DEFAULT_DAYS,
    session: Session = Depends(get_session),
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Compatibility entry point for routers importing the descriptive name."""
    return get_age_distribution(days=days, session=session, as_of=as_of)


def calculate_age_distribution(
    days: int = DEFAULT_DAYS,
    session: Session = Depends(get_session),
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Compatibility entry point for callers using a calculation-style name."""
    return get_age_distribution(days=days, session=session, as_of=as_of)


def _self_test() -> None:
    as_of = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [
        ("HIGH", as_of - timedelta(days=1)),
        ("MEDIUM", as_of - timedelta(days=10)),
        ("LOW", as_of - timedelta(days=45)),
        ("CRITICAL", as_of - timedelta(days=200)),
    ]

    result = _build_distribution(rows, days=365, as_of=as_of)
    counts = [bucket["count"] for bucket in result["buckets"]]

    assert result["total"] == 4
    assert sum(counts) == 4
    assert counts[0] == 1
    assert counts[4] == 1
    assert len(result["by_severity"]) == 4
    assert all(
        sum(bucket["count"] for bucket in severity_buckets) == 1
        for severity_buckets in result["by_severity"].values()
    )
    print("PASS")


if __name__ == "__main__":
    _self_test()