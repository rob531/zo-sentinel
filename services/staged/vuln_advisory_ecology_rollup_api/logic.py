"""Logic for the vulnerability advisory ecosystem rollup service."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


def _severity_bucket(severity: object) -> str:
    """Return the canonical severity bucket for an advisory severity."""
    value = str(severity or "").strip().lower()
    if value in {"critical", "high", "medium", "low"}:
        return value
    return "unknown"


def get_ecosystem_rollup(
    db: Session,
    ecosystem: Optional[str] = None,
    feed: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, object]:
    """Compute advisory counts by ecosystem using the real advisory table.

    ``limit`` limits the number of ecosystem rows returned. Counts and the
    total advisory count are calculated for all advisories matching the
    optional ecosystem and feed filters.
    """
    statement = select(VulnAdvisory)
    if ecosystem:
        statement = statement.where(VulnAdvisory.ecosystem == ecosystem)
    if feed:
        statement = statement.where(VulnAdvisory.feed == feed)

    advisories = list(db.execute(statement).scalars().all())

    ecosystem_rows: Dict[str, Dict[str, object]] = {}
    feed_counts: Dict[str, int] = {}
    latest_published: Dict[str, datetime] = {}
    stalest_fetched: Optional[datetime] = None

    for advisory in advisories:
        ecosystem_name = advisory.ecosystem or "unknown"
        feed_name = advisory.feed or "unknown"

        row = ecosystem_rows.setdefault(
            ecosystem_name,
            {
                "ecosystem": ecosystem_name,
                "advisory_count": 0,
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
                "unknown_count": 0,
                "latest_published_at": None,
            },
        )
        row["advisory_count"] = int(row["advisory_count"]) + 1
        bucket = _severity_bucket(advisory.severity)
        row[f"{bucket}_count"] = int(row[f"{bucket}_count"]) + 1

        feed_counts[feed_name] = feed_counts.get(feed_name, 0) + 1

        published_at = advisory.published_at
        if published_at is not None:
            current_latest = latest_published.get(ecosystem_name)
            if current_latest is None or published_at > current_latest:
                latest_published[ecosystem_name] = published_at

        fetched_at = advisory.fetched_at
        if fetched_at is not None:
            if stalest_fetched is None or fetched_at < stalest_fetched:
                stalest_fetched = fetched_at

    for ecosystem_name, row in ecosystem_rows.items():
        row["latest_published_at"] = latest_published.get(ecosystem_name)

    ordered = sorted(
        ecosystem_rows.values(),
        key=lambda item: (
            int(item["advisory_count"]),
            str(item["ecosystem"]),
        ),
        reverse=True,
    )
    max_rows = max(0, int(limit))
    ecosystem_result: List[Dict[str, object]] = ordered[:max_rows]

    feeds = [
        {"feed": feed_name, "count": count}
        for feed_name, count in sorted(feed_counts.items())
    ]

    return {
        "ecosystems": ecosystem_result,
        "total_advisories": len(advisories),
        "feeds": feeds,
        "stalest_fetched_at": stalest_fetched,
    }


# Keep a descriptive alias for callers that use the service name directly.
compute_ecosystem_rollup = get_ecosystem_rollup


if __name__ == "__main__":
    from datetime import timezone

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
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    now = datetime.now(timezone.utc)
    test_db.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="osv",
                summary="First advisory",
                severity="critical",
                ecosystem="npm",
                package="example-a",
                affected_ranges=["<1.0"],
                aliases=[],
                source_url="https://example.test/1",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="osv",
                summary="Second advisory",
                severity="HIGH",
                ecosystem="npm",
                package="example-b",
                affected_ranges=["<2.0"],
                aliases=[],
                source_url="https://example.test/2",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="github",
                summary="Third advisory",
                severity="medium",
                ecosystem="pypi",
                package="example-c",
                affected_ranges=["<3.0"],
                aliases=[],
                source_url="https://example.test/3",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="hash-3",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()

    @test_app.get("/api/vuln/ecosystems")
    def ecosystem_endpoint(
        ecosystem: Optional[str] = None,
        feed: Optional[str] = None,
        limit: int = 50,
        db: Session = Depends(get_session),
    ) -> Dict[str, object]:
        return get_ecosystem_rollup(
            db,
            ecosystem=ecosystem,
            feed=feed,
            limit=limit,
        )

    test_app.dependency_overrides[get_session] = lambda: test_db
    response = TestClient(test_app).get("/api/vuln/ecosystems")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_advisories"] == 3
    assert len(payload["ecosystems"]) == 2
    for item in payload["ecosystems"]:
        assert (
            item["critical_count"]
            + item["high_count"]
            + item["medium_count"]
            + item["low_count"]
            + item["unknown_count"]
            == item["advisory_count"]
        )
    assert len(payload["feeds"]) == 2
    print("PASS")