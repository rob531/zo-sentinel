from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_advisory_age_data(session: Session, days: int = 30) -> dict[str, Any]:
    rows = session.execute(
        select(
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
        )
    ).all()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    feed_groups: dict[str, dict[str, Any]] = {}
    severity_groups: dict[str, dict[str, int]] = {}
    total_advisories = 0
    recent_advisories = 0

    for feed, severity, published_at, fetched_at in rows:
        total_advisories += 1
        feed_name = feed if feed not in (None, "") else "unknown"
        severity_name = severity if severity not in (None, "") else "unknown"

        feed_group = feed_groups.setdefault(
            feed_name,
            {
                "feed": feed_name,
                "total": 0,
                "recent": 0,
                "oldest_published_at": None,
                "newest_published_at": None,
                "_oldest": None,
                "_newest": None,
                "_fetch_lags": [],
            },
        )
        feed_group["total"] += 1

        severity_group = severity_groups.setdefault(
            severity_name,
            {"severity": severity_name, "total": 0, "recent": 0},
        )
        severity_group["total"] += 1

        if published_at is not None:
            normalized_published = _utc_datetime(published_at)
            if normalized_published >= cutoff:
                feed_group["recent"] += 1
                severity_group["recent"] += 1
                recent_advisories += 1

            oldest = feed_group["_oldest"]
            newest = feed_group["_newest"]
            if oldest is None or normalized_published < oldest[0]:
                feed_group["_oldest"] = (normalized_published, published_at)
            if newest is None or normalized_published > newest[0]:
                feed_group["_newest"] = (normalized_published, published_at)

            if fetched_at is not None:
                lag_hours = (
                    _utc_datetime(fetched_at) - normalized_published
                ).total_seconds() / 3600.0
                feed_group["_fetch_lags"].append(lag_hours)

    by_feed: list[dict[str, Any]] = []
    for feed_name in sorted(feed_groups):
        group = feed_groups[feed_name]
        oldest = group.pop("_oldest")
        newest = group.pop("_newest")
        lags = group.pop("_fetch_lags")

        group["oldest_published_at"] = oldest[1].isoformat() if oldest else None
        group["newest_published_at"] = newest[1].isoformat() if newest else None
        group["median_fetch_lag_hours"] = float(median(lags)) if lags else None
        by_feed.append(group)

    by_severity = [
        severity_groups[name]
        for name in sorted(severity_groups)
    ]

    return {
        "days": days,
        "total_advisories": total_advisories,
        "recent_advisories": recent_advisories,
        "by_feed": by_feed,
        "by_severity": by_severity,
    }


get_advisory_age = get_advisory_age_data
get_publish_age = get_advisory_age_data
get_publish_age_data = get_advisory_age_data
get_advisory_publish_age = get_advisory_age_data
get_advisory_publish_age_data = get_advisory_age_data
get_vuln_advisory_publish_age = get_advisory_age_data
calculate_advisory_age = get_advisory_age_data


if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with test_session_factory() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id="test-1",
                    feed="alpha",
                    summary="Recent high severity advisory",
                    severity="high",
                    ecosystem="npm",
                    package="example-a",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.invalid/1",
                    published_at=now - timedelta(days=2),
                    fetched_at=now - timedelta(days=1),
                    identities=[],
                    content_hash="hash-1",
                ),
                VulnAdvisory(
                    id="test-2",
                    feed="alpha",
                    summary="Older low severity advisory",
                    severity="low",
                    ecosystem="npm",
                    package="example-b",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.invalid/2",
                    published_at=now - timedelta(days=40),
                    fetched_at=now - timedelta(days=39),
                    identities=[],
                    content_hash="hash-2",
                ),
                VulnAdvisory(
                    id="test-3",
                    feed="beta",
                    summary="Advisory with unknown severity",
                    severity=None,
                    ecosystem="pypi",
                    package="example-c",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.invalid/3",
                    published_at=now - timedelta(days=1),
                    fetched_at=now,
                    identities=[],
                    content_hash="hash-3",
                ),
                VulnAdvisory(
                    id="test-4",
                    feed="beta",
                    summary="Advisory with unknown publication date",
                    severity="high",
                    ecosystem="pypi",
                    package="example-d",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.invalid/4",
                    published_at=None,
                    fetched_at=now,
                    identities=[],
                    content_hash="hash-4",
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/vuln/advisory-age")
    def advisory_age(
        days: int = 30,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_advisory_age_data(session, days)

    with TestClient(local_app) as client:
        response = client.get("/api/vuln/advisory-age", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 4
        assert any(
            group["severity"] == "unknown" for group in payload["by_severity"]
        )
        assert sum(group["total"] for group in payload["by_severity"]) == 4
        assert payload["recent_advisories"] <= payload["total_advisories"]

    print("PASS")