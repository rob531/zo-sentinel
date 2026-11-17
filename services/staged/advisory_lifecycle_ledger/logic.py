"""logic.py -- advisory lifecycle computation over the real application data layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


_LIFECYCLE_BUCKETS = (
    "never_linked",
    "linked_within_30d",
    "linked_within_90d",
    "stale_unfetched",
)

# Feed cadences are expressed in days.  Unknown feeds use a conservative
# weekly cadence rather than treating an advisory as fresh indefinitely.
_FEED_CADENCE_DAYS = {
    "npm": 1,
    "pypi": 1,
    "osv": 1,
    "github": 1,
    "nvd": 1,
    "cve": 1,
    "default": 7,
}


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Optional[datetime], now: datetime) -> Optional[float]:
    value = _as_utc(value)
    if value is None:
        return None
    return (now - value).total_seconds() / 86400.0


def _feed_cadence_days(feed: Optional[str]) -> int:
    if not feed:
        return _FEED_CADENCE_DAYS["default"]
    return _FEED_CADENCE_DAYS.get(feed.lower(), _FEED_CADENCE_DAYS["default"])


def lifecycle_snapshot(db: Session, days: int = 30) -> Dict[str, object]:
    """Return advisory lifecycle buckets and severity counts.

    The advisory population is the complete current ``vuln_advisories`` table.
    ``days`` is retained in the result so callers can associate the snapshot
    with their requested reporting window; the lifecycle thresholds themselves
    are the named 30-day and 90-day buckets in the service contract.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 3650))

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(VulnAdvisory, VulnLink).outerjoin(
            VulnLink, VulnLink.advisory_id == VulnAdvisory.id
        )
    ).all()

    advisories: Dict[int, Dict[str, object]] = {}
    for advisory, link in rows:
        entry = advisories.get(advisory.id)
        if entry is None:
            entry = {
                "advisory": advisory,
                "links": [],
            }
            advisories[advisory.id] = entry
        if link is not None:
            entry["links"].append(link)

    buckets: Dict[str, int] = {name: 0 for name in _LIFECYCLE_BUCKETS}
    severity_mix: Dict[str, int] = {}
    top_unlinked: List[Dict[str, object]] = []

    for entry in advisories.values():
        advisory = entry["advisory"]
        links = entry["links"]
        severity = advisory.severity or "unknown"
        severity_mix[severity] = severity_mix.get(severity, 0) + 1

        if not links:
            buckets["never_linked"] += 1
            top_unlinked.append(
                {
                    "advisory_id": advisory.id,
                    "feed": advisory.feed,
                    "severity": advisory.severity,
                    "package": advisory.package,
                    "published_at": advisory.published_at,
                }
            )
            continue

        fetched_age = _age_days(advisory.fetched_at, now)
        cadence = _feed_cadence_days(advisory.feed)
        if fetched_age is not None and fetched_age > cadence * 2:
            buckets["stale_unfetched"] += 1
            continue

        link_ages = [
            age
            for age in (_age_days(link.linked_at, now) for link in links)
            if age is not None
        ]
        if not link_ages:
            continue

        newest_link_age = min(link_ages)
        if newest_link_age <= 30:
            buckets["linked_within_30d"] += 1
        elif newest_link_age <= 90:
            buckets["linked_within_90d"] += 1

    top_unlinked.sort(
        key=lambda item: (
            item["published_at"] is not None,
            item["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    top_unlinked = top_unlinked[:10]

    return {
        "days": days,
        "generated_at": now.isoformat(),
        "buckets": buckets,
        "severity_mix": severity_mix,
        "top_unlinked": top_unlinked,
    }


# Compatibility names for thin routers and existing callers.
compute_lifecycle_snapshot = lifecycle_snapshot
get_lifecycle_snapshot = lifecycle_snapshot
build_lifecycle_report = lifecycle_snapshot


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)

    with Session(engine) as db:
        now = datetime.now(timezone.utc)
        advisories = [
            VulnAdvisory(
                id=1,
                feed="npm",
                source_url="https://example.test/advisories/1",
                severity="high",
                ecosystem="npm",
                package="example-one",
                published_at=now - timedelta(days=2),
                fetched_at=now,
                content_hash="hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="npm",
                source_url="https://example.test/advisories/2",
                severity="medium",
                ecosystem="npm",
                package="example-two",
                published_at=now - timedelta(days=3),
                fetched_at=now,
                content_hash="hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="osv",
                source_url="https://example.test/advisories/3",
                severity="critical",
                ecosystem="pypi",
                package="example-three",
                published_at=now - timedelta(days=4),
                fetched_at=now,
                content_hash="hash-3",
            ),
            VulnAdvisory(
                id=4,
                feed="osv",
                source_url="https://example.test/advisories/4",
                severity="low",
                ecosystem="pypi",
                package="example-four",
                published_at=now - timedelta(days=5),
                fetched_at=now,
                content_hash="hash-4",
            ),
        ]
        db.add_all(advisories)
        db.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=101,
                    linked_at=now - timedelta(days=1),
                    match_basis="package",
                    match_confidence=0.99,
                    match_value="example-one",
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id=102,
                    linked_at=now - timedelta(days=2),
                    match_basis="package",
                    match_confidence=0.98,
                    match_value="example-two",
                ),
            ]
        )
        db.commit()

        test_app = FastAPI()

        @test_app.get("/api/advisories/lifecycle")
        def lifecycle_endpoint(db: Session = Depends(get_session)):
            return lifecycle_snapshot(db, days=30)

        def override_get_session():
            yield db

        test_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(test_app).get(
            "/api/advisories/lifecycle", params={"days": 30}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["buckets"]["never_linked"] == 2, payload
        assert payload["top_unlinked"], payload
        assert sum(payload["severity_mix"].values()) == 4, payload

    print("PASS")