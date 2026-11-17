"""Fetch-lag computation for vulnerability advisories using the real ORM models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_fetch_lag(
    db: Session,
    hours: int = 24,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """Return per-feed fetch-lag buckets for advisories fetched within the window."""
    if hours <= 0:
        raise ValueError("hours must be greater than zero")

    current = _utc(now or datetime.now(timezone.utc))
    cutoff = current - timedelta(hours=hours)
    advisories = db.execute(
        select(VulnAdvisory).where(VulnAdvisory.fetched_at >= cutoff)
    ).scalars().all()

    buckets: Dict[str, Dict[str, object]] = {}
    for advisory in advisories:
        feed = advisory.feed or "unknown"
        row = buckets.setdefault(
            feed,
            {
                "feed": feed,
                "total": 0,
                "fresh": 0,
                "delayed": 0,
                "stale": 0,
                "expired": 0,
                "max_lag_seconds": 0,
                "oldest_published_at": None,
            },
        )

        fetched_at = _utc(advisory.fetched_at)
        published_at = _utc(advisory.published_at) if advisory.published_at else None
        lag = max(timedelta(0), fetched_at - published_at) if published_at else timedelta(0)

        row["total"] = int(row["total"]) + 1
        if lag <= timedelta(hours=1):
            row["fresh"] = int(row["fresh"]) + 1
        elif lag <= timedelta(hours=24):
            row["delayed"] = int(row["delayed"]) + 1
        elif lag <= timedelta(days=7):
            row["stale"] = int(row["stale"]) + 1
        else:
            row["expired"] = int(row["expired"]) + 1

        row["max_lag_seconds"] = max(
            int(row["max_lag_seconds"]),
            int(lag.total_seconds()),
        )
        oldest = row["oldest_published_at"]
        if published_at and (oldest is None or published_at < oldest):
            row["oldest_published_at"] = published_at

    feeds = [buckets[feed] for feed in sorted(buckets)]
    summary = {
        "total": sum(int(row["total"]) for row in feeds),
        "stale": sum(int(row["stale"]) for row in feeds),
        "expired": sum(int(row["expired"]) for row in feeds),
    }
    return {"window_hours": hours, "feeds": feeds, "summary": summary}


get_advisory_fetch_lag = get_fetch_lag
get_fetch_lag_board = get_fetch_lag
get_advisory_fetch_lag_board = get_fetch_lag


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    VulnAdvisory.__table__.create(test_engine)

    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    seeded = [
        VulnAdvisory(
            id=1,
            feed="alpha",
            summary="fresh advisory",
            severity="low",
            ecosystem="pypi",
            package="alpha-fresh",
            published_at=base_time - timedelta(minutes=30),
            fetched_at=base_time - timedelta(minutes=5),
            source_url="https://example.test/alpha/1",
        ),
        VulnAdvisory(
            id=2,
            feed="alpha",
            summary="stale advisory",
            severity="medium",
            ecosystem="npm",
            package="alpha-stale",
            published_at=base_time - timedelta(days=2),
            fetched_at=base_time - timedelta(hours=1),
            source_url="https://example.test/alpha/2",
        ),
        VulnAdvisory(
            id=3,
            feed="beta",
            summary="delayed advisory",
            severity="medium",
            ecosystem="pypi",
            package="beta-delayed",
            published_at=base_time - timedelta(hours=2),
            fetched_at=base_time - timedelta(minutes=30),
            source_url="https://example.test/beta/3",
        ),
        VulnAdvisory(
            id=4,
            feed="beta",
            summary="expired advisory",
            severity="high",
            ecosystem="npm",
            package="beta-expired",
            published_at=base_time - timedelta(days=8),
            fetched_at=base_time - timedelta(hours=2),
            source_url="https://example.test/beta/4",
        ),
    ]

    with TestSession() as session:
        session.add_all(seeded)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/vuln/advisories/fetch-lag")
    def fetch_lag_endpoint(hours: int = 24, db: Session = Depends(get_session)):
        return {
            "generated_at": datetime.now(timezone.utc),
            **get_fetch_lag(db, hours),
        }

    response = TestClient(that_app).get(
        "/api/vuln/advisories/fetch-lag",
        params={"hours": 24},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["feeds"]) == 2
    stale_expired = sum(feed["stale"] + feed["expired"] for feed in payload["feeds"])
    assert stale_expired == 2
    assert payload["summary"]["stale"] + payload["summary"]["expired"] == 2
    print("PASS")