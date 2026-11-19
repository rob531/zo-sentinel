"""logic.py -- advisory feed health data and computation layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        candidate = value.strip()
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return candidate
    return value.isoformat()


def _hours_between(newer: Any, older: Any) -> float:
    if newer is None or older is None:
        return 0.0

    def as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    newer_dt = as_datetime(newer)
    older_dt = as_datetime(older)
    if newer_dt is None or older_dt is None:
        return 0.0
    try:
        return max(0.0, (newer_dt - older_dt).total_seconds() / 3600.0)
    except TypeError:
        return max(0.0, (newer_dt.replace(tzinfo=None) - older_dt.replace(tzinfo=None)).total_seconds() / 3600.0)


def get_feed_health(
    db: Session,
    freshness_threshold_hours: float = 24,
) -> Dict[str, Any]:
    """Return freshness information for each vulnerability advisory feed."""
    fresh_expression = case(
        (
            and_(
                VulnAdvisory.published_at.is_not(None),
                VulnAdvisory.fetched_at.is_not(None),
                VulnAdvisory.fetched_at >= VulnAdvisory.published_at,
            ),
            1,
        ),
        else_=0,
    )
    stale_expression = case(
        (
            and_(
                VulnAdvisory.published_at.is_not(None),
                VulnAdvisory.fetched_at.is_not(None),
                VulnAdvisory.fetched_at < VulnAdvisory.published_at,
            ),
            1,
        ),
        else_=0,
    )

    rows = db.execute(
        select(
            VulnAdvisory.feed.label("feed"),
            func.count(VulnAdvisory.id).label("advisory_count"),
            func.max(VulnAdvisory.published_at).label("newest_published_at"),
            func.max(VulnAdvisory.fetched_at).label("newest_fetched_at"),
            func.min(VulnAdvisory.fetched_at).label("oldest_fetched_at"),
            func.sum(fresh_expression).label("fresh_count"),
            func.sum(stale_expression).label("stale_count"),
        )
        .group_by(VulnAdvisory.feed)
        .order_by(VulnAdvisory.feed)
    ).all()

    feeds: list[Dict[str, Any]] = []
    stale_feeds: list[str] = []
    total_advisories = 0

    for row in rows:
        feed_name = row.feed or "unknown"
        advisory_count = int(row.advisory_count or 0)
        total_advisories += advisory_count
        staleness_hours = _hours_between(
            row.newest_published_at,
            row.newest_fetched_at,
        )
        is_stale = staleness_hours > freshness_threshold_hours
        if is_stale:
            stale_feeds.append(feed_name)

        feeds.append(
            {
                "feed": feed_name,
                "advisory_count": advisory_count,
                "newest_published_at": _iso_timestamp(row.newest_published_at),
                "newest_fetched_at": _iso_timestamp(row.newest_fetched_at),
                "oldest_fetched_at": _iso_timestamp(row.oldest_fetched_at),
                "staleness_hours": staleness_hours,
                "fresh_count": int(row.fresh_count or 0),
                "stale_count": int(row.stale_count or 0),
            }
        )

    return {
        "feeds": feeds,
        "total_advisories": total_advisories,
        "stale_feeds": stale_feeds,
        "overall_status": "stale" if stale_feeds else "fresh",
    }


advisory_feed_health = get_feed_health
get_advisory_feed_health = get_feed_health


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()

    now = datetime(2025, 1, 1, 12, 0, 0)
    session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="current-feed",
                severity="HIGH",
                ecosystem="pypi",
                package="example-current",
                published_at=now - timedelta(hours=1),
                fetched_at=now,
                source_url="https://example.invalid/current/1",
                content_hash="current-hash",
            ),
            VulnAdvisory(
                id=2,
                feed="stale-feed",
                severity="CRITICAL",
                ecosystem="npm",
                package="example-stale",
                published_at=now + timedelta(hours=72),
                fetched_at=now,
                source_url="https://example.invalid/stale/1",
                content_hash="stale-hash",
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    @test_app.get("/api/advisories/feed-health")
    def advisory_feed_health_endpoint(
        db: Session = Depends(get_session),
        freshness_threshold_hours: int = 24,
    ) -> Dict[str, Any]:
        result = get_feed_health(db, freshness_threshold_hours)
        result["freshness_threshold_hours"] = freshness_threshold_hours
        return result

    test_app.dependency_overrides[get_session] = lambda: session
    response = TestClient(test_app).get("/api/advisories/feed-health")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["feeds"]) == 2
    assert "stale-feed" in payload["stale_feeds"]
    print("PASS")
    session.close()
    engine.dispose()