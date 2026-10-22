import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


class FeedFreshnessResponse(BaseModel):
    total: int
    stale_after_hours: int
    feeds: list[dict]
    severity_mix: list[dict]
    ecosystems: list[dict]


def get_feed_freshness(
    feed: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    stale_hours: int = Query(default=24),
    db: Session = Depends(get_session),
):
    """Return advisory feed freshness, severity, and ecosystem statistics."""
    stmt = db.query(VulnAdvisory)
    if feed:
        stmt = stmt.filter(VulnAdvisory.feed == feed)
    if ecosystem:
        stmt = stmt.filter(VulnAdvisory.ecosystem == ecosystem)

    advisories = stmt.all()
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=stale_hours)

    feed_groups = defaultdict(list)
    severity_counts = defaultdict(int)
    ecosystem_counts = defaultdict(int)

    for advisory in advisories:
        feed_groups[advisory.feed].append(advisory)
        severity_counts[advisory.severity or "unknown"] += 1
        ecosystem_counts[advisory.ecosystem or "unknown"] += 1

    feeds = []
    for feed_name, items in sorted(feed_groups.items()):
        fetched_times = [
            item.fetched_at for item in items if item.fetched_at is not None
        ]
        published_times = [
            item.published_at for item in items if item.published_at is not None
        ]
        stale_count = sum(
            1
            for item in items
            if item.fetched_at is None or item.fetched_at < threshold
        )
        feeds.append(
            {
                "feed": feed_name,
                "advisory_count": len(items),
                "stale_count": stale_count,
                "latest_fetched_at": max(fetched_times).isoformat() if fetched_times else None,
                "latest_published_at": max(published_times).isoformat() if published_times else None,
                "oldest_fetched_at": min(fetched_times).isoformat() if fetched_times else None,
            }
        )

    return {
        "total": len(advisories),
        "stale_after_hours": stale_hours,
        "feeds": feeds,
        "severity_mix": [
            {"severity": severity, "count": count}
            for severity, count in sorted(severity_counts.items())
        ],
        "ecosystems": [
            {"ecosystem": ecosystem_name, "count": count}
            for ecosystem_name, count in sorted(ecosystem_counts.items())
        ],
    }


app = FastAPI()


@app.get("/api/advisories/feed_freshness", response_model=FeedFreshnessResponse)
def feed_freshness_endpoint(
    feed: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    stale_hours: int = Query(default=24),
    db: Session = Depends(get_session),
):
    return get_feed_freshness(feed, ecosystem, stale_hours, db)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    class InMemorySession:
        def __init__(self, advisories):
            self.advisories = advisories

        def query(self, model):
            return self

        def filter(self, *conditions):
            return self

        def all(self):
            return self.advisories

    now = datetime.now(timezone.utc)
    sample = [
        VulnAdvisory(
            id=1,
            feed="nvd",
            severity="critical",
            ecosystem="pypi",
            fetched_at=now,
            published_at=now,
        ),
        VulnAdvisory(
            id=2,
            feed="nvd",
            severity="high",
            ecosystem="npm",
            fetched_at=now - timedelta(hours=48),
            published_at=now - timedelta(days=1),
        ),
        VulnAdvisory(
            id=3,
            feed="osv",
            severity="medium",
            ecosystem="pypi",
            fetched_at=now,
            published_at=now,
        ),
        VulnAdvisory(
            id=4,
            feed="osv",
            severity="low",
            ecosystem="npm",
            fetched_at=now,
            published_at=now,
        ),
    ]

    db = InMemorySession(sample)
    # Use dependency override so the endpoint uses the in-memory session.
    app.dependency_overrides[get_session] = lambda: db
    try:
        response = TestClient(app).get("/api/advisories/feed_freshness")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["feeds"]) == 2
        assert payload["total"] == 4
        assert sum(item["count"] for item in payload["severity_mix"]) == payload["total"]
        print("PASS")
    finally:
        app.dependency_overrides.clear()