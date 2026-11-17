"""logic.py -- advisory ageing data and computation over the real data layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


_BUCKETS = ("<=7d", "<=30d", "<=90d", "<=365d", ">365d")


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_bucket(published_at: Optional[datetime], now: datetime) -> str:
    if published_at is None:
        return ">365d"
    age = now - published_at
    if age <= timedelta(days=7):
        return "<=7d"
    if age <= timedelta(days=30):
        return "<=30d"
    if age <= timedelta(days=90):
        return "<=90d"
    if age <= timedelta(days=365):
        return "<=365d"
    return ">365d"


def vuln_advisory_ageing(
    db: Session,
    ecosystem: Optional[str] = None,
    days: int = 90,
) -> Dict[str, object]:
    """Return the ageing profile for advisories in the requested scope.

    ``days`` is the lookback window for published advisories. All date and
    ecosystem predicates use SQLAlchemy-bound expressions.
    """
    try:
        lookback_days = int(days)
    except (TypeError, ValueError):
        lookback_days = 90
    lookback_days = max(0, lookback_days)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    query = select(
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
        VulnAdvisory.feed,
    ).where(VulnAdvisory.published_at >= cutoff)

    if ecosystem is not None:
        query = query.where(VulnAdvisory.ecosystem == ecosystem)

    rows = db.execute(query).all()

    bucket_counts: Dict[str, int] = {bucket: 0 for bucket in _BUCKETS}
    feed_counts: Dict[str, int] = {}
    stale_fetch_count = 0

    for published_at, fetched_at, feed in rows:
        published_at = _as_utc(published_at)
        fetched_at = _as_utc(fetched_at)

        bucket = _age_bucket(published_at, now)
        bucket_counts[bucket] += 1

        feed_name = feed or "unknown"
        feed_counts[feed_name] = feed_counts.get(feed_name, 0) + 1

        if published_at is not None and fetched_at is not None:
            if fetched_at < published_at + timedelta(days=1):
                stale_fetch_count += 1

    top_feeds = [
        {"feed": feed, "count": count}
        for feed, count in sorted(
            feed_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:10]
    ]

    return {
        "ecosystem": ecosystem,
        "days": lookback_days,
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in _BUCKETS
        ],
        "top_feeds": top_feeds,
        "stale_fetch_count": stale_fetch_count,
        "total": len(rows),
    }


# Compatibility aliases for service callers using the common naming variants.
get_vuln_advisory_ageing = vuln_advisory_ageing
build_vuln_advisory_ageing_profile = vuln_advisory_ageing
get_ageing_profile = vuln_advisory_ageing


if __name__ == "__main__":
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
    TestingSession = sessionmaker(bind=engine)

    session = TestingSession()
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="python-feed",
                summary="one",
                severity="HIGH",
                ecosystem="python",
                package="one",
                affected_ranges="<1.0",
                aliases="",
                source_url="https://example.invalid/1",
                published_at=now - timedelta(days=1),
                fetched_at=now - timedelta(days=1),
                identities="",
                content_hash="1",
            ),
            VulnAdvisory(
                id=2,
                feed="python-feed",
                summary="two",
                severity="MEDIUM",
                ecosystem="python",
                package="two",
                affected_ranges="<2.0",
                aliases="",
                source_url="https://example.invalid/2",
                published_at=now - timedelta(days=10),
                fetched_at=now - timedelta(days=12),
                identities="",
                content_hash="2",
            ),
            VulnAdvisory(
                id=3,
                feed="osv",
                summary="three",
                severity="LOW",
                ecosystem="python",
                package="three",
                affected_ranges="<3.0",
                aliases="",
                source_url="https://example.invalid/3",
                published_at=now - timedelta(days=40),
                fetched_at=now - timedelta(days=40),
                identities="",
                content_hash="3",
            ),
            VulnAdvisory(
                id=4,
                feed="npm-feed",
                summary="four",
                severity="CRITICAL",
                ecosystem="npm",
                package="four",
                affected_ranges="<4.0",
                aliases="",
                source_url="https://example.invalid/4",
                published_at=now - timedelta(days=20),
                fetched_at=now - timedelta(days=20),
                identities="",
                content_hash="4",
            ),
            VulnAdvisory(
                id=5,
                feed="osv",
                summary="five",
                severity="HIGH",
                ecosystem="npm",
                package="five",
                affected_ranges="<5.0",
                aliases="",
                source_url="https://example.invalid/5",
                published_at=now - timedelta(days=100),
                fetched_at=now - timedelta(days=100),
                identities="",
                content_hash="5",
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    @test_app.get("/api/vuln/advisory-ageing")
    def advisory_ageing_route(
        ecosystem: Optional[str] = None,
        days: int = 90,
        db: Session = Depends(get_session),
    ):
        return vuln_advisory_ageing(db, ecosystem, days)

    test_app.dependency_overrides[get_session] = lambda: session

    response = TestClient(test_app).get(
        "/api/vuln/advisory-ageing",
        params={"ecosystem": "python", "days": 90},
    )
    assert response.status_code == 200
    payload = response.json()
    assert sum(item["count"] for item in payload["buckets"]) == payload["total"]
    assert payload["top_feeds"]
    assert payload["top_feeds"] == sorted(
        payload["top_feeds"],
        key=lambda item: (-item["count"], item["feed"]),
    )
    print("PASS")