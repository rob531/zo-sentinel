from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp_string(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def summarize_lag(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        feed_value = row.get("feed")
        feed = str(feed_value) if feed_value is not None else "unknown"
        grouped.setdefault(feed, []).append(row)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    feeds: list[dict] = []

    for feed in sorted(grouped):
        feed_rows = grouped[feed]
        lags: list[float] = []
        published_times: list[datetime] = []
        unfetched_count = 0
        stale_over_24h = 0

        for row in feed_rows:
            published = _parse_timestamp(row.get("published_at"))
            fetched = _parse_timestamp(row.get("fetched_at"))

            if published is not None:
                published_utc = _utc(published)
                published_times.append(published_utc)
                if fetched is None and published_utc < cutoff:
                    stale_over_24h += 1

            if fetched is None:
                unfetched_count += 1
            elif published is not None:
                lag_hours = (_utc(fetched) - _utc(published)).total_seconds() / 3600
                if lag_hours >= 0:
                    lags.append(lag_hours)

        mean_lag_hours = sum(lags) / len(lags) if lags else None
        if lags:
            ordered_lags = sorted(lags)
            percentile_index = max(0, ceil(0.9 * len(ordered_lags)) - 1)
            p90_lag_hours = ordered_lags[percentile_index]
        else:
            p90_lag_hours = None

        feeds.append(
            {
                "feed": feed,
                "advisory_count": len(feed_rows),
                "unfetched_count": unfetched_count,
                "mean_lag_hours": mean_lag_hours,
                "p90_lag_hours": p90_lag_hours,
                "newest_published_at": _timestamp_string(
                    max(published_times) if published_times else None
                ),
                "oldest_published_at": _timestamp_string(
                    min(published_times) if published_times else None
                ),
                "stale_over_24h": stale_over_24h,
            }
        )

    return {
        "feeds": feeds,
        "totals": {
            "feeds": len(feeds),
            "advisories": sum(feed["advisory_count"] for feed in feeds),
            "unfetched": sum(feed["unfetched_count"] for feed in feeds),
        },
    }


def fetch_lag_rows(session: Session) -> list[dict]:
    statement = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.source_url,
    )
    return [dict(row) for row in session.execute(statement).mappings().all()]


def get_lag_summary(session: Session = Depends(get_session)) -> dict:
    return summarize_lag(fetch_lag_rows(session))


fetch_lag_summary = get_lag_summary
get_advisory_fetch_lag = get_lag_summary
get_lag_data = get_lag_summary


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE vuln_advisories (
                id INTEGER PRIMARY KEY,
                feed TEXT NOT NULL,
                published_at TEXT NOT NULL,
                fetched_at TEXT,
                severity TEXT,
                ecosystem TEXT,
                source_url TEXT
            )
            """
        )

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.execute(
            select(VulnAdvisory.id).where(VulnAdvisory.id == -1)
        )
        session.connection().exec_driver_sql(
            """
            INSERT INTO vuln_advisories
                (id, feed, published_at, fetched_at, severity, ecosystem, source_url)
            VALUES
                (1, 'alpha', ?, NULL, 'high', 'pypi', 'https://example.test/1'),
                (2, 'alpha', ?, ?, 'medium', 'pypi', 'https://example.test/2'),
                (3, 'beta', ?, ?, 'critical', 'npm', 'https://example.test/3')
            """,
            (
                (now - timedelta(hours=48)).isoformat(),
                (now - timedelta(hours=2)).isoformat(),
                now.isoformat(),
                (now - timedelta(hours=48)).isoformat(),
                now.isoformat(),
            ),
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/vuln/advisory-lag")
    def advisory_lag(session: Session = Depends(get_session)) -> dict:
        return get_lag_summary(session)

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisory-lag")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["feeds"]) == 2
        alpha = next(feed for feed in payload["feeds"] if feed["feed"] == "alpha")
        assert alpha["unfetched_count"] == 1
        assert isinstance(alpha["mean_lag_hours"], float)
        assert alpha["mean_lag_hours"] >= 0

    print("PASS")