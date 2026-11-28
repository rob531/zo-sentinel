"""Data and computation layer for vulnerability advisory ingest lag."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lag_hours(
    published_at: Optional[datetime], fetched_at: Optional[datetime]
) -> Optional[float]:
    published = _as_utc(published_at)
    fetched = _as_utc(fetched_at)
    if published is None or fetched is None:
        return None
    return (fetched - published).total_seconds() / 3600.0


def get_advisory_lag(db: Session) -> Dict[str, Any]:
    """Return advisory ingest-lag metrics grouped by feed.

    An advisory is considered unlinked when no matching row exists in
    ``vuln_links``.  Only advisories with a fetched timestamp contribute to
    lag statistics; missing fetched timestamps are reported as stale.
    """
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    advisories = list(db.execute(select(VulnAdvisory)).scalars())
    links = list(db.execute(select(VulnLink)).scalars())

    linked_advisory_ids = {link.advisory_id for link in links}
    linked_servers_by_feed: Dict[str, set[Any]] = {}

    advisory_by_id = {advisory.id: advisory for advisory in advisories}
    for link in links:
        advisory = advisory_by_id.get(link.advisory_id)
        if advisory is None:
            continue
        feed = advisory.feed or "unknown"
        linked_servers_by_feed.setdefault(feed, set()).add(link.server_id)

    grouped: Dict[str, list[VulnAdvisory]] = {}
    for advisory in advisories:
        feed = advisory.feed or "unknown"
        grouped.setdefault(feed, []).append(advisory)

    feeds: list[Dict[str, Any]] = []
    total_advisories = 0
    total_unlinked = 0

    for feed in sorted(grouped):
        feed_advisories = grouped[feed]
        published_24h = 0
        published_7d = 0
        lag_values: list[float] = []
        oldest_unfetched: Optional[datetime] = None

        for advisory in feed_advisories:
            total_advisories += 1
            published = _as_utc(advisory.published_at)
            if published is not None:
                if published >= day_ago:
                    published_24h += 1
                if published >= week_ago:
                    published_7d += 1

            lag = _lag_hours(advisory.published_at, advisory.fetched_at)
            if lag is not None:
                lag_values.append(lag)
            else:
                if published is not None and (
                    oldest_unfetched is None or published < oldest_unfetched
                ):
                    oldest_unfetched = published

            if advisory.id not in linked_advisory_ids:
                total_unlinked += 1

        max_lag = max(lag_values) if lag_values else 0.0
        avg_lag = sum(lag_values) / len(lag_values) if lag_values else 0.0

        feeds.append(
            {
                "feed": feed,
                "advisories_total": len(feed_advisories),
                "published_24h": published_24h,
                "published_7d": published_7d,
                "max_lag_hours": float(max_lag),
                "avg_lag_hours": float(avg_lag),
                "linked_servers": len(linked_servers_by_feed.get(feed, set())),
                "unlinked_advisories": sum(
                    1
                    for advisory in feed_advisories
                    if advisory.id not in linked_advisory_ids
                ),
                "oldest_unfetched_published_at": oldest_unfetched,
            }
        )

    return {
        "feeds": feeds,
        "totals": {
            "advisories_total": total_advisories,
            "unlinked_advisories": total_unlinked,
        },
    }


def compute_advisory_lag(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the advisory-lag computation."""
    return get_advisory_lag(db)


def get_feed_lag(db: Session) -> Dict[str, Any]:
    """Compatibility alias returning the complete feed report."""
    return get_advisory_lag(db)


def advisory_lag(db: Session) -> Dict[str, Any]:
    """Compatibility alias returning the complete feed report."""
    return get_advisory_lag(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    local_app = FastAPI()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/vuln/advisory-lag")
    def advisory_lag_endpoint(db: Session = Depends(get_session)):
        return get_advisory_lag(db)

    db = SessionLocal()
    try:
        published = datetime.now(timezone.utc) - timedelta(hours=3)
        older = datetime.now(timezone.utc) - timedelta(days=2)
        db.add_all(
            [
                VulnAdvisory(
                    id=1001,
                    feed="alpha",
                    severity="high",
                    ecosystem="pypi",
                    package="example-one",
                    published_at=published,
                    fetched_at=published + timedelta(hours=1),
                    content_hash="alpha-one",
                    source_url="https://example.invalid/alpha-one",
                ),
                VulnAdvisory(
                    id=1002,
                    feed="alpha",
                    severity="medium",
                    ecosystem="npm",
                    package="example-two",
                    published_at=older,
                    fetched_at=None,
                    content_hash="alpha-two",
                    source_url="https://example.invalid/alpha-two",
                ),
                VulnAdvisory(
                    id=1003,
                    feed="beta",
                    severity="critical",
                    ecosystem="pypi",
                    package="example-three",
                    published_at=published,
                    fetched_at=published + timedelta(hours=2),
                    content_hash="beta-one",
                    source_url="https://example.invalid/beta-one",
                ),
            ]
        )
        db.add_all(
            [
                VulnLink(
                    id=2001,
                    advisory_id=1001,
                    server_id=3001,
                    match_confidence=0.95,
                    linked_at=datetime.now(timezone.utc),
                    match_basis="package",
                    match_value="example-one",
                ),
                VulnLink(
                    id=2002,
                    advisory_id=1001,
                    server_id=3002,
                    match_confidence=0.90,
                    linked_at=datetime.now(timezone.utc),
                    match_basis="package",
                    match_value="example-one",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    with TestClient(local_app) as client:
        response = client.get("/api/vuln/advisory-lag")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["feeds"]) == 2
        assert payload["totals"]["advisories_total"] == 3
        assert payload["totals"]["unlinked_advisories"] >= 1
        assert all(
            isinstance(feed["max_lag_hours"], (int, float))
            for feed in payload["feeds"]
        )

    print("PASS")