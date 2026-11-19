"""HTTP surface for vulnerability advisory feed coverage."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import VulnAdvisory, VulnLink

try:
    from .logic import feed_coverage_summary
except ImportError:
    feed_coverage_summary = None

router = APIRouter(prefix="/api", tags=["advisory-feed-coverage"])


class FeedCoverage(BaseModel):
    feed: str
    severity_counts: dict[str, int]
    advisories: int
    linked_servers: int
    median_lag_days: float


class FeedCoverageResponse(BaseModel):
    generated_at: datetime
    feeds: list[FeedCoverage]
    total_advisories: int
    total_linked_servers: int


def _fallback_feed_coverage_summary(db: Session) -> dict[str, Any]:
    advisory_rows = db.execute(
        select(
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id),
        ).group_by(VulnAdvisory.feed, VulnAdvisory.severity)
    ).all()

    advisory_counts: dict[str, int] = {}
    severity_counts: dict[str, dict[str, int]] = {}
    for feed, severity, count in advisory_rows:
        advisory_counts[feed] = advisory_counts.get(feed, 0) + count
        severity_counts.setdefault(feed, {})[severity or "unknown"] = count

    linked_server_rows = db.execute(
        select(
            VulnAdvisory.feed,
            func.count(func.distinct(VulnLink.server_id)),
        )
        .select_from(VulnLink)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnAdvisory.feed)
    ).all()
    linked_servers = {feed: count for feed, count in linked_server_rows}

    advisories = db.execute(select(VulnAdvisory)).scalars().all()
    now = datetime.now(timezone.utc)
    lags_by_feed: dict[str, list[float]] = {}
    for advisory in advisories:
        fetched_at = advisory.fetched_at
        if fetched_at is None:
            continue
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        lags_by_feed.setdefault(advisory.feed, []).append(
            max(0.0, (now - fetched_at).total_seconds() / 86400.0)
        )

    feeds = []
    for feed in sorted(advisory_counts):
        lags = lags_by_feed.get(feed, [])
        feeds.append(
            {
                "feed": feed,
                "severity_counts": severity_counts.get(feed, {}),
                "advisories": advisory_counts[feed],
                "linked_servers": linked_servers.get(feed, 0),
                "median_lag_days": float(median(lags)) if lags else 0.0,
            }
        )

    return {
        "generated_at": now,
        "feeds": feeds,
        "total_advisories": sum(advisory_counts.values()),
        "total_linked_servers": sum(linked_servers.values()),
    }


@router.get("/vuln/feed-coverage", response_model=FeedCoverageResponse)
def get_feed_coverage(db: Session = Depends(get_session)) -> FeedCoverageResponse:
    summary = (
        feed_coverage_summary(db)
        if feed_coverage_summary is not None
        else _fallback_feed_coverage_summary(db)
    )
    if isinstance(summary, dict):
        raw_feeds = summary.get("feeds", [])
        generated_at = summary.get("generated_at") or datetime.now(timezone.utc)
    else:
        raw_feeds = summary
        generated_at = datetime.now(timezone.utc)

    feeds = [
        item if isinstance(item, FeedCoverage) else FeedCoverage.parse_obj(item)
        for item in raw_feeds
    ]
    return FeedCoverageResponse(
        generated_at=generated_at,
        feeds=feeds,
        total_advisories=sum(feed.advisories for feed in feeds),
        total_linked_servers=sum(feed.linked_servers for feed in feeds),
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        db.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="alpha",
                    severity="critical",
                    ecosystem="pypi",
                    package="example-one",
                    published_at=now,
                    fetched_at=now,
                    source_url="https://example.invalid/advisory/1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="alpha",
                    severity="high",
                    ecosystem="npm",
                    package="example-two",
                    published_at=now,
                    fetched_at=now - datetime.resolution * 86400,
                    source_url="https://example.invalid/advisory/2",
                ),
                VulnAdvisory(
                    id=3,
                    feed="beta",
                    severity="medium",
                    ecosystem="go",
                    package="example-three",
                    published_at=now,
                    fetched_at=now - datetime.resolution * 2 * 86400,
                    source_url="https://example.invalid/advisory/3",
                ),
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=10,
                    match_basis="package",
                    match_confidence=0.95,
                    match_value="example-one",
                    linked_at=now,
                ),
                VulnLink(
                    id=2,
                    advisory_id=999,
                    server_id=20,
                    match_basis="package",
                    match_confidence=0.90,
                    match_value="missing-advisory",
                    linked_at=now,
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/vuln/feed-coverage")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["feeds"]) == 2, payload
    assert payload["total_advisories"] == 3, payload
    alpha = next(feed for feed in payload["feeds"] if feed["feed"] == "alpha")
    beta = next(feed for feed in payload["feeds"] if feed["feed"] == "beta")
    assert alpha["advisories"] == 2, payload
    assert alpha["linked_servers"] == 1, payload
    assert beta["linked_servers"] == 0, payload
    print("PASS")