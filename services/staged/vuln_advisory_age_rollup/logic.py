from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


_UNKNOWN = "unknown"


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_vuln_advisory_age_rollup(db: Session, days: int = 30) -> dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")

    advisories = list(db.execute(select(VulnAdvisory)).scalars())
    links = list(db.execute(select(VulnLink)).scalars())

    severity_counts: Counter[str] = Counter()
    ecosystem_counts: Counter[str] = Counter()
    feed_counts: Counter[str] = Counter()
    advisory_severity: dict[Any, str] = {}
    linked_advisory_ids: set[Any] = set()
    servers_by_severity: defaultdict[str, set[Any]] = defaultdict(set)

    for advisory in advisories:
        severity = advisory.severity or _UNKNOWN
        ecosystem = advisory.ecosystem or _UNKNOWN
        feed = advisory.feed or _UNKNOWN

        severity_counts[severity] += 1
        ecosystem_counts[ecosystem] += 1
        feed_counts[feed] += 1
        advisory_severity[advisory.id] = severity

    stale_sync_count = 0
    window = timedelta(days=days)

    for advisory in advisories:
        if advisory.published_at is None or advisory.fetched_at is None:
            continue
        age = _utc_datetime(advisory.fetched_at) - _utc_datetime(advisory.published_at)
        if age > window:
            stale_sync_count += 1

    low_confidence_link_count = 0

    for link in links:
        linked_advisory_ids.add(link.advisory_id)

        if link.match_confidence is not None and link.match_confidence < 0.5:
            low_confidence_link_count += 1

        severity = advisory_severity.get(link.advisory_id)
        if severity is not None:
            servers_by_severity[severity].add(link.server_id)

    unlinked_count = sum(
        1 for advisory in advisories if advisory.id not in linked_advisory_ids
    )

    return {
        "days": days,
        "total_advisories": len(advisories),
        "by_severity": dict(sorted(severity_counts.items())),
        "by_ecosystem": dict(sorted(ecosystem_counts.items())),
        "by_feed": dict(sorted(feed_counts.items())),
        "stale_sync_count": stale_sync_count,
        "unlinked_count": unlinked_count,
        "low_confidence_link_count": low_confidence_link_count,
        "linked_servers_by_severity": {
            severity: len(servers_by_severity.get(severity, set()))
            for severity in sorted(severity_counts)
        },
    }


def get_advisory_age_rollup(db: Session, days: int = 30) -> dict[str, Any]:
    return get_vuln_advisory_age_rollup(db, days)


def get_advisory_age_summary(db: Session, days: int = 30) -> dict[str, Any]:
    return get_vuln_advisory_age_rollup(db, days)


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    session = session_factory()
    session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="osv",
                severity="critical",
                ecosystem="pypi",
                package="alpha",
                source_url="https://example.test/advisories/1",
                published_at=now - timedelta(days=40),
                fetched_at=now - timedelta(days=2),
            ),
            VulnAdvisory(
                id=2,
                feed="github",
                severity="high",
                ecosystem="npm",
                package="beta",
                source_url="https://example.test/advisories/2",
                published_at=now - timedelta(days=3),
                fetched_at=now - timedelta(days=2),
            ),
            VulnAdvisory(
                id=3,
                feed="osv",
                severity="medium",
                ecosystem="pypi",
                package="gamma",
                source_url="https://example.test/advisories/3",
                published_at=now - timedelta(days=3),
                fetched_at=now - timedelta(days=2),
            ),
            VulnAdvisory(
                id=4,
                feed="github",
                severity="low",
                ecosystem="npm",
                package="delta",
                source_url="https://example.test/advisories/4",
                published_at=now - timedelta(days=3),
                fetched_at=now - timedelta(days=2),
            ),
            VulnAdvisory(
                id=5,
                feed="osv",
                severity="critical",
                ecosystem="pypi",
                package="epsilon",
                source_url="https://example.test/advisories/5",
                published_at=now - timedelta(days=3),
                fetched_at=now - timedelta(days=2),
            ),
        ]
    )
    session.add_all(
        [
            VulnLink(
                id=1,
                advisory_id=1,
                server_id=101,
                match_basis="package",
                match_confidence=0.9,
                match_value="alpha",
            ),
            VulnLink(
                id=2,
                advisory_id=2,
                server_id=102,
                match_basis="package",
                match_confidence=0.2,
                match_value="beta",
            ),
            VulnLink(
                id=3,
                advisory_id=3,
                server_id=103,
                match_basis="package",
                match_confidence=0.8,
                match_value="gamma",
            ),
            VulnLink(
                id=4,
                advisory_id=4,
                server_id=101,
                match_basis="package",
                match_confidence=0.7,
                match_value="delta",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        with session_factory() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/vuln/advisory-age")
    def advisory_age(days: int = 30, db: Session = Depends(get_session)):
        return get_vuln_advisory_age_rollup(db, days)

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisory-age", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 5
        assert payload["by_severity"]["critical"] == 2
        assert payload["stale_sync_count"] == 1
        assert payload["unlinked_count"] == 1
        assert payload["low_confidence_link_count"] == 1

    print("PASS")


if __name__ == "__main__":
    main()