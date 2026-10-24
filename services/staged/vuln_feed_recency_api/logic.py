from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fetch_lag_hours(
    fetched_at: datetime | None, published_at: datetime | None
) -> float | None:
    fetched = _normalize_datetime(fetched_at)
    published = _normalize_datetime(published_at)
    if fetched is None or published is None:
        return None
    return (fetched - published).total_seconds() / 3600.0


def calculate_feed_recency(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
        )
    ).all()

    feeds: dict[str, dict[str, Any]] = {}
    total_advisories = 0

    for feed, severity, ecosystem, published_at, fetched_at in rows:
        if feed is None:
            continue

        feed = str(feed)
        total_advisories += 1
        entry = feeds.setdefault(
            feed,
            {
                "feed": feed,
                "advisories": 0,
                "newest_published_at": None,
                "oldest_published_at": None,
                "max_fetch_lag_hours": None,
                "mean_fetch_lag_hours": None,
                "by_severity": {},
                "by_ecosystem": {},
                "_fetch_lags": [],
            },
        )

        entry["advisories"] += 1

        published = _normalize_datetime(published_at)
        if published is not None:
            newest = entry["newest_published_at"]
            oldest = entry["oldest_published_at"]
            entry["newest_published_at"] = (
                published if newest is None or published > newest else newest
            )
            entry["oldest_published_at"] = (
                published if oldest is None or published < oldest else oldest
            )

        lag = _fetch_lag_hours(fetched_at, published_at)
        if lag is not None:
            entry["_fetch_lags"].append(lag)

        if severity is not None:
            severity_key = str(severity)
            entry["by_severity"][severity_key] = (
                entry["by_severity"].get(severity_key, 0) + 1
            )

        if ecosystem is not None:
            ecosystem_key = str(ecosystem)
            entry["by_ecosystem"][ecosystem_key] = (
                entry["by_ecosystem"].get(ecosystem_key, 0) + 1
            )

    feed_rows: list[dict[str, Any]] = []
    for feed in sorted(feeds):
        entry = feeds[feed]
        lags = entry.pop("_fetch_lags")
        if lags:
            entry["max_fetch_lag_hours"] = max(lags)
            entry["mean_fetch_lag_hours"] = sum(lags) / len(lags)
        entry["by_severity"] = dict(sorted(entry["by_severity"].items()))
        entry["by_ecosystem"] = dict(sorted(entry["by_ecosystem"].items()))
        feed_rows.append(entry)

    return {
        "feeds": feed_rows,
        "totals": {
            "advisories": total_advisories,
            "feeds": len(feed_rows),
        },
    }


def get_feed_recency(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return calculate_feed_recency(session)


get_vuln_feed_recency = get_feed_recency
get_feed_recency_data = calculate_feed_recency
get_feed_recency_stats = calculate_feed_recency
fetch_feed_recency = calculate_feed_recency


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    test_session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="feed-a",
                summary="Advisory one",
                severity="HIGH",
                ecosystem="PyPI",
                package="example-one",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/1",
                published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                fetched_at=datetime(2024, 1, 1, 3, tzinfo=timezone.utc),
                identities={},
                content_hash="hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="feed-a",
                summary="Advisory two",
                severity="HIGH",
                ecosystem="npm",
                package="example-two",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/2",
                published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                fetched_at=datetime(2024, 1, 2, 5, tzinfo=timezone.utc),
                identities={},
                content_hash="hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="feed-b",
                summary="Advisory three",
                severity="LOW",
                ecosystem="PyPI",
                package="example-three",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/3",
                published_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                fetched_at=datetime(2024, 1, 3, 1, 30, tzinfo=timezone.utc),
                identities={},
                content_hash="hash-3",
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/vuln/feeds/recency")
    def feed_recency_route(session: Session = Depends(get_session)):
        return calculate_feed_recency(session)

    response = TestClient(test_app).get("/api/vuln/feeds/recency")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["advisories"] == 3
    assert payload["totals"]["feeds"] == 2
    feed_a = next(row for row in payload["feeds"] if row["feed"] == "feed-a")
    assert feed_a["by_severity"]["HIGH"] == 2
    assert feed_a["max_fetch_lag_hours"] is not None

    test_session.close()
    test_engine.dispose()
    print("PASS")