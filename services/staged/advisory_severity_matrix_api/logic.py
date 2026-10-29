from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import get_session
from app.models import VulnAdvisory
from sqlalchemy import select


_MAX_LIMIT = 5000
_AGE_BUCKETS = ("0-7d", "8-30d", "31-90d", ">90d")


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_bucket(published_at: datetime | str | None, now: datetime) -> str | None:
    published = _as_utc(published_at)
    if published is None:
        return None

    age_days = max(0, (now - published).total_seconds() / 86400)
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return ">90d"


def get_severity_matrix(
    session: Any,
    limit: int = _MAX_LIMIT,
    feed: str | None = None,
) -> dict[str, Any]:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("limit must be an integer")
    if limit < 1 or limit > _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")

    statement = select(
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.feed,
        VulnAdvisory.published_at,
    )
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)
    statement = statement.limit(limit)

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)

    severity_counts: dict[str, dict[str, int]] = {}
    ecosystem_names: set[str] = set()
    severity_names: set[str] = set()
    feed_counts: dict[str, int] = {}
    age_buckets = {bucket: 0 for bucket in _AGE_BUCKETS}
    latest_published: datetime | None = None
    total = 0

    for severity_value, ecosystem_value, feed_value, published_value in rows:
        total += 1

        if severity_value is not None:
            severity = str(severity_value)
            severity_names.add(severity)
            if ecosystem_value is not None:
                ecosystem = str(ecosystem_value)
                ecosystem_names.add(ecosystem)
                severity_counts.setdefault(severity, {})[ecosystem] = (
                    severity_counts.setdefault(severity, {}).get(ecosystem, 0) + 1
                )

        if feed_value is not None:
            feed_name = str(feed_value)
            feed_counts[feed_name] = feed_counts.get(feed_name, 0) + 1

        bucket = _age_bucket(published_value, now)
        if bucket is not None:
            age_buckets[bucket] += 1

        published = _as_utc(published_value)
        if published is not None and (
            latest_published is None or published > latest_published
        ):
            latest_published = published

    severities = sorted(severity_names)
    ecosystems = sorted(ecosystem_names)
    matrix = {
        severity: {
            ecosystem: severity_counts.get(severity, {}).get(ecosystem, 0)
            for ecosystem in ecosystems
        }
        for severity in severities
    }

    return {
        "total": total,
        "severities": severities,
        "ecosystems": ecosystems,
        "matrix": matrix,
        "feeds": [
            {"feed": feed_name, "count": feed_counts[feed_name]}
            for feed_name in sorted(feed_counts)
        ],
        "age_buckets": age_buckets,
        "last_published_at": (
            latest_published.isoformat() if latest_published is not None else None
        ),
    }


def get_advisory_severity_matrix(
    session: Any,
    limit: int = _MAX_LIMIT,
    feed: str | None = None,
) -> dict[str, Any]:
    return get_severity_matrix(session, limit=limit, feed=feed)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    feed VARCHAR,
                    published_at DATETIME
                )
                """
            )
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, severity, ecosystem, feed, published_at)
                VALUES
                    (1, 'critical', 'npm', 'alpha', :published1),
                    (2, 'critical', 'pypi', 'beta', :published2),
                    (3, 'low', 'npm', 'alpha', :published3)
                """
            ),
            {
                "published1": (now - timedelta(days=1)).isoformat(sep=" "),
                "published2": (now - timedelta(days=2)).isoformat(sep=" "),
                "published3": (now - timedelta(days=100)).isoformat(sep=" "),
            },
        )

    test_session = TestSession()
    test_app = FastAPI()

    @test_app.get("/api/advisories/severity-matrix")
    def severity_matrix_endpoint(
        limit: int = Query(default=5000, ge=1, le=5000),
        feed: str | None = Query(default=None),
        session: Any = Depends(get_session),
    ) -> dict[str, Any]:
        return get_severity_matrix(session, limit=limit, feed=feed)

    test_app.dependency_overrides[get_session] = lambda: test_session

    with TestClient(test_app) as client:
        response = client.get("/api/advisories/severity-matrix")
        assert response.status_code == 200
        payload = response.json()
        assert sum(payload["matrix"]["critical"].values()) == 2
        assert sum(payload["age_buckets"].values()) == 3
        assert len(payload["feeds"]) == 2

    test_session.close()
    test_engine.dispose()
    print("PASS")