"""logic.py -- advisory freshness lag computation over the real app data layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


def get_advisory_lag(db: Session) -> Dict[str, Any]:
    """Return publication-to-fetch lag aggregates grouped by feed and severity."""
    rows = db.execute(
        select(
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
        )
    ).all()

    groups: Dict[tuple[str, str], Dict[str, Any]] = {}
    total_advisories = 0

    for feed_value, severity_value, published_at, fetched_at in rows:
        total_advisories += 1

        if published_at is None or fetched_at is None:
            continue

        feed = str(feed_value) if feed_value is not None else "unknown"
        severity = str(severity_value) if severity_value is not None else "unknown"
        lag_hours = (fetched_at - published_at).total_seconds() / 3600.0

        key = (feed, severity)
        group = groups.setdefault(
            key,
            {
                "feed": feed,
                "severity": severity,
                "advisory_count": 0,
                "_lag_sum": 0.0,
                "_lag_max": None,
            },
        )
        group["advisory_count"] += 1
        group["_lag_sum"] += lag_hours
        group["_lag_max"] = (
            lag_hours
            if group["_lag_max"] is None
            else max(group["_lag_max"], lag_hours)
        )

    feeds: List[Dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        count = group["advisory_count"]
        feeds.append(
            {
                "feed": group["feed"],
                "severity": group["severity"],
                "advisory_count": count,
                "avg_lag_hours": float(group["_lag_sum"] / count),
                "max_lag_hours": float(group["_lag_max"]),
            }
        )

    if feeds:
        worst = max(feeds, key=lambda item: item["avg_lag_hours"])
        worst_feed: Optional[str] = worst["feed"]
        worst_avg_lag_hours = float(worst["avg_lag_hours"])
    else:
        worst_feed = None
        worst_avg_lag_hours = 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_advisories": total_advisories,
        "worst_feed": worst_feed,
        "worst_avg_lag_hours": worst_avg_lag_hours,
        "feeds": feeds,
    }


def build_advisory_lag(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for service callers."""
    return get_advisory_lag(db)


def build_advisory_freshness_lag(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for freshness-gate callers."""
    return get_advisory_lag(db)


def get_advisory_freshness_lag(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for named service consumers."""
    return get_advisory_lag(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    seeded = [
        VulnAdvisory(
            id=1,
            feed="feed-a",
            severity="high",
            ecosystem="pypi",
            package="alpha",
            published_at=now,
            fetched_at=now.replace(hour=2),
            source_url="https://example.test/a",
            content_hash="a",
        ),
        VulnAdvisory(
            id=2,
            feed="feed-a",
            severity="low",
            ecosystem="npm",
            package="beta",
            published_at=now,
            fetched_at=now.replace(hour=4),
            source_url="https://example.test/b",
            content_hash="b",
        ),
        VulnAdvisory(
            id=3,
            feed="feed-b",
            severity="high",
            ecosystem="npm",
            package="gamma",
            published_at=now,
            fetched_at=now.replace(hour=6),
            source_url="https://example.test/c",
            content_hash="c",
        ),
        VulnAdvisory(
            id=4,
            feed="feed-b",
            severity="low",
            ecosystem="pypi",
            package="delta",
            published_at=now,
            fetched_at=now.replace(hour=8),
            source_url="https://example.test/d",
            content_hash="d",
        ),
        VulnAdvisory(
            id=5,
            feed="feed-a",
            severity="critical",
            ecosystem="npm",
            package="epsilon",
            published_at=None,
            fetched_at=now.replace(hour=10),
            source_url="https://example.test/e",
            content_hash="e",
        ),
    ]
    testing_session.add_all(seeded)
    testing_session.commit()

    local_app = FastAPI()

    def override_session():
        yield testing_session

    local_app.dependency_overrides[get_session] = override_session

    result = get_advisory_lag(testing_session)
    assert result["total_advisories"] == 5
    assert len(result["feeds"]) == 4
    assert all(
        group["advisory_count"] == 1 for group in result["feeds"]
    )
    assert all(
        group["severity"] != "critical" for group in result["feeds"]
    )
    feed_a_high = next(
        group
        for group in result["feeds"]
        if group["feed"] == "feed-a" and group["severity"] == "high"
    )
    assert abs(feed_a_high["avg_lag_hours"] - 2.0) < 0.01

    testing_session.close()
    engine.dispose()
    print("PASS")