"""Logic for the CVE advisory publish-lag API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VulnAdvisory


def _utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime for SQLite and Postgres values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    """Calculate a percentile using the linear interpolation used by Postgres."""
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


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2.0)


def _distribution(
    advisories: List[VulnAdvisory],
    now: datetime,
) -> Dict[str, Any]:
    lags: List[float] = []
    stale_count = 0
    missing_published_at = 0

    for advisory in advisories:
        if advisory.fetched_at is not None:
            fetched_at = _utc(advisory.fetched_at)
            if fetched_at < now - timedelta(hours=72):
                stale_count += 1

        if advisory.published_at is None:
            missing_published_at += 1
            continue

        if advisory.fetched_at is None:
            continue

        lag_hours = (
            _utc(advisory.published_at) - _utc(advisory.fetched_at)
        ).total_seconds() / 3600.0
        lags.append(float(lag_hours))

    return {
        "count": len(lags),
        "min_lag_hours": min(lags) if lags else None,
        "median_lag_hours": _median(lags),
        "p90_lag_hours": _percentile(lags, 0.90),
        "max_lag_hours": max(lags) if lags else None,
        "stale_count": stale_count,
        "missing_published_at": missing_published_at,
    }


def compute_publish_lag(
    db: Session,
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compute publish-lag distributions by feed and in aggregate.

    The age window is based on publication time when available, with fetched
    time used for advisories whose publication time is missing.  A lag is the
    number of hours from fetching an advisory to its publication timestamp.
    """
    current_time = _utc(now or datetime.now(timezone.utc))
    query = select(VulnAdvisory)
    if days is not None and days > 0:
        cutoff = current_time - timedelta(days=days)
        advisories = db.execute(query).scalars().all()
        advisories = [
            advisory
            for advisory in advisories
            if (
                advisory.published_at is not None
                and _utc(advisory.published_at) >= cutoff
            )
            or (
                advisory.published_at is None
                and advisory.fetched_at is not None
                and _utc(advisory.fetched_at) >= cutoff
            )
        ]
    else:
        advisories = db.execute(query).scalars().all()

    by_feed: Dict[str, List[VulnAdvisory]] = {}
    for advisory in advisories:
        feed = advisory.feed or "unknown"
        by_feed.setdefault(feed, []).append(advisory)

    feeds: List[Dict[str, Any]] = []
    for feed in sorted(by_feed):
        distribution = _distribution(by_feed[feed], current_time)
        feeds.append({"feed": feed, **distribution})

    overall = _distribution(advisories, current_time)
    feeds.append({"feed": "overall", **overall})

    severity_counts: Dict[str, int] = {}
    for advisory in advisories:
        severity = advisory.severity or "unknown"
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    severity_mix = [
        {"severity": severity, "count": count}
        for severity, count in sorted(severity_counts.items())
    ]

    return {
        "days": int(days),
        "generated_at": current_time.isoformat(),
        "feeds": feeds,
        "severity_mix": severity_mix,
        "overall": overall,
    }


def get_publish_lag(
    db: Session,
    days: int = 30,
) -> Dict[str, Any]:
    """Compatibility entry point for routers and contracts."""
    return compute_publish_lag(db, days=days)


def get_publish_lag_stats(
    db: Session,
    days: int = 30,
) -> Dict[str, Any]:
    """Compatibility entry point for the publish-lag statistics API."""
    return compute_publish_lag(db, days=days)


def publish_lag_distribution(
    db: Session,
    days: int = 30,
) -> Dict[str, Any]:
    """Compatibility entry point for distribution-oriented callers."""
    return compute_publish_lag(db, days=days)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session

    engine = create_engine("sqlite://")
    VulnAdvisory.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    current_time = datetime.now(timezone.utc)
    for index, hours in enumerate((0, 24, 96), start=1):
        session.add(
            VulnAdvisory(
                id=index,
                feed="OSV" if index <= 2 else "GHSA",
                severity=("high", "medium", "low")[index - 1],
                ecosystem="PyPI",
                package=f"package-{index}",
                source_url=f"https://example.test/advisory/{index}",
                published_at=current_time,
                fetched_at=current_time - timedelta(hours=hours),
                content_hash=f"hash-{index}",
            )
        )
    session.commit()

    result = compute_publish_lag(session, days=365)
    overall = result["overall"]
    assert overall["median_lag_hours"] == 24.0
    assert overall["stale_count"] == 1
    assert len(result["severity_mix"]) == 3

    # Keep the local application dependency override explicit for contract
    # execution against an in-memory SQLite database.
    from fastapi import FastAPI

    that_app = FastAPI()

    def override_session():
        return session

    that_app.dependency_overrides[get_session] = override_session
    session.close()
    print("PASS")