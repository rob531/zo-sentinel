from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base as _Base


BUCKET_NAMES = ("lt_7d", "7_30d", "31_90d", "gt_90d", "unknown")
SEVERITY_NAMES = ("low", "medium", "high", "critical", "unknown")


class SeverityMix(BaseModel):
    low: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    critical: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)


class AgeBucket(BaseModel):
    bucket: str
    count: int = Field(ge=0)
    by_severity: SeverityMix


class FeedCount(BaseModel):
    feed: str
    count: int = Field(ge=0)


class AgeBucketsResponse(BaseModel):
    total: int = Field(ge=0)
    buckets: list[AgeBucket]
    feeds: list[FeedCount]


def _normalize_severity(value: Any) -> str:
    if value is None:
        return "unknown"
    severity = str(value).strip().lower()
    return severity if severity in SEVERITY_NAMES else "unknown"


def _normalize_feed(value: Any) -> str:
    if value is None:
        return "unknown"
    feed = str(value).strip()
    return feed if feed else "unknown"


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: float) -> str:
    if age_days < 7:
        return "lt_7d"
    if age_days <= 30:
        return "7_30d"
    if age_days <= 90:
        return "31_90d"
    return "gt_90d"


def build_age_buckets(
    rows: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    current_time = _as_utc(now) or datetime.now(timezone.utc)
    counts = {bucket: 0 for bucket in BUCKET_NAMES}
    severity_counts = {
        bucket: {severity: 0 for severity in SEVERITY_NAMES}
        for bucket in BUCKET_NAMES
    }
    feed_counts: dict[str, int] = {}

    for row in rows:
        published_at = _as_utc(row.get("published_at"))
        if published_at is None:
            bucket = "unknown"
        else:
            age_days = (current_time - published_at).total_seconds() / 86400.0
            bucket = _bucket_for_age(age_days)

        severity = _normalize_severity(row.get("severity"))
        feed = _normalize_feed(row.get("feed"))
        counts[bucket] += 1
        severity_counts[bucket][severity] += 1
        feed_counts[feed] = feed_counts.get(feed, 0) + 1

    return {
        "total": len(rows),
        "buckets": [
            {
                "bucket": bucket,
                "count": counts[bucket],
                "by_severity": severity_counts[bucket],
            }
            for bucket in BUCKET_NAMES
        ],
        "feeds": [
            {"feed": feed, "count": count}
            for feed, count in sorted(feed_counts.items())
        ],
    }


app = FastAPI(title="Vulnerability Advisory Age Buckets API")


@app.get(
    "/api/vuln/advisories/age-buckets",
    response_model=AgeBucketsResponse,
)
def get_advisory_age_buckets(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = session.execute(
        text(
            """
            SELECT published_at, severity, feed
            FROM vuln_advisories
            """
        )
    )
    rows = [dict(row) for row in result.mappings().all()]
    return build_age_buckets(rows)


def _run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker = __import__(
        "sqlalchemy.orm", fromlist=["sessionmaker"]
    ).sessionmaker
    testing_session = sessionmaker(bind=engine)

    try:
        with testing_session() as session:
            session.execute(
                text(
                    """
                    CREATE TABLE vuln_advisories (
                        id INTEGER PRIMARY KEY,
                        feed TEXT,
                        summary TEXT,
                        severity TEXT,
                        ecosystem TEXT,
                        package TEXT,
                        affected_ranges TEXT,
                        aliases TEXT,
                        source_url TEXT,
                        published_at DATETIME,
                        fetched_at DATETIME,
                        identities TEXT,
                        content_hash TEXT
                    )
                    """
                )
            )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            rows = [
                {
                    "feed": "osv",
                    "summary": "Recently published advisory",
                    "severity": "high",
                    "ecosystem": "PyPI",
                    "package": "example-recent",
                    "affected_ranges": ">=1.0.0,<1.2.0",
                    "aliases": "CVE-2026-0001",
                    "source_url": "https://example.test/osv/1",
                    "published_at": now - timedelta(days=10),
                    "fetched_at": now,
                    "identities": "OSV-1",
                    "content_hash": "hash-1",
                },
                {
                    "feed": "nvd",
                    "summary": "Medium-age advisory",
                    "severity": "critical",
                    "ecosystem": "npm",
                    "package": "example-medium",
                    "affected_ranges": "<2.0.0",
                    "aliases": "CVE-2026-0002",
                    "source_url": "https://example.test/nvd/2",
                    "published_at": now - timedelta(days=40),
                    "fetched_at": now,
                    "identities": "NVD-2",
                    "content_hash": "hash-2",
                },
                {
                    "feed": "osv",
                    "summary": "Old advisory",
                    "severity": "medium",
                    "ecosystem": "Go",
                    "package": "example-old",
                    "affected_ranges": "<3.0.0",
                    "aliases": "CVE-2026-0003",
                    "source_url": "https://example.test/osv/3",
                    "published_at": now - timedelta(days=100),
                    "fetched_at": now,
                    "identities": "OSV-3",
                    "content_hash": "hash-3",
                },
                {
                    "feed": "nvd",
                    "summary": "Very recent advisory",
                    "severity": "low",
                    "ecosystem": "PyPI",
                    "package": "example-new",
                    "affected_ranges": "<4.0.0",
                    "aliases": "CVE-2026-0004",
                    "source_url": "https://example.test/nvd/4",
                    "published_at": now - timedelta(days=5),
                    "fetched_at": now,
                    "identities": "NVD-4",
                    "content_hash": "hash-4",
                },
                {
                    "feed": "osv",
                    "summary": "Advisory without publication date",
                    "severity": "critical",
                    "ecosystem": "RubyGems",
                    "package": "example-undated",
                    "affected_ranges": "<5.0.0",
                    "aliases": "CVE-2026-0005",
                    "source_url": "https://example.test/osv/5",
                    "published_at": None,
                    "fetched_at": now,
                    "identities": "OSV-5",
                    "content_hash": "hash-5",
                },
            ]

            for row in rows:
                session.execute(
                    text(
                        """
                        INSERT INTO vuln_advisories (
                            feed,
                            summary,
                            severity,
                            ecosystem,
                            package,
                            affected_ranges,
                            aliases,
                            source_url,
                            published_at,
                            fetched_at,
                            identities,
                            content_hash
                        ) VALUES (
                            :feed,
                            :summary,
                            :severity,
                            :ecosystem,
                            :package,
                            :affected_ranges,
                            :aliases,
                            :source_url,
                            :published_at,
                            :fetched_at,
                            :identities,
                            :content_hash
                        )
                        """
                    ),
                    row,
                )
            session.commit()

        def override_get_session():
            with testing_session() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        try:
            with TestClient(app) as client:
                response = client.get("/api/vuln/advisories/age-buckets")
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["total"] == 5, payload

                buckets = {bucket["bucket"]: bucket for bucket in payload["buckets"]}
                assert set(buckets) == set(BUCKET_NAMES), payload
                assert buckets["unknown"]["count"] == 1, payload

                for bucket in payload["buckets"]:
                    severity_total = sum(bucket["by_severity"].values())
                    assert severity_total == bucket["count"], payload
                    assert set(bucket["by_severity"]) == set(SEVERITY_NAMES), payload

                feeds = {feed["feed"]: feed["count"] for feed in payload["feeds"]}
                assert feeds == {"nvd": 2, "osv": 3}, payload
        finally:
            app.dependency_overrides.clear()
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_contract()