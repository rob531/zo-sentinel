"""Threat-intelligence recency aggregation over the real application models."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _utc_now_naive() -> datetime:
    """Return a UTC timestamp suitable for both PostgreSQL and SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_recency(db: Session = Depends(get_session), hours: int = 24) -> Dict[str, Any]:
    """Return per-source threat-intelligence counts for a recent time window.

    The window is represented by a parameterised cutoff timestamp rather than
    a database-specific interval expression, making the query portable between
    PostgreSQL and SQLite.
    """
    try:
        window_hours = int(hours)
    except (TypeError, ValueError) as exc:
        raise ValueError("hours must be an integer") from exc

    if window_hours < 1:
        raise ValueError("hours must be at least 1")

    now = _utc_now_naive()
    cutoff = now - timedelta(hours=window_hours)

    rows = db.execute(
        select(
            ThreatIntelRef.source,
            ThreatIntelRef.fetched_at,
            ThreatIntelRef.is_aggregator,
        ).where(ThreatIntelRef.fetched_at >= cutoff)
    ).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for source, fetched_at, is_aggregator in rows:
        source_name = source or "unknown"
        if source_name not in grouped:
            grouped[source_name] = {
                "source": source_name,
                "total": 0,
                "fresh": 0,
                "day": 0,
                "week": 0,
                "stale": 0,
                "newest_fetched_at": None,
                "aggregator_count": 0,
            }

        item = grouped[source_name]
        item["total"] += 1

        age = now - fetched_at
        if age < timedelta(hours=1):
            item["fresh"] += 1
        elif age < timedelta(hours=24):
            item["day"] += 1
        elif age < timedelta(days=7):
            item["week"] += 1
        else:
            item["stale"] += 1

        if is_aggregator:
            item["aggregator_count"] += 1

        if item["newest_fetched_at"] is None or fetched_at > item["newest_fetched_at"]:
            item["newest_fetched_at"] = fetched_at

    by_source = [grouped[name] for name in sorted(grouped)]
    return {
        "hours": window_hours,
        "total": len(rows),
        "by_source": by_source,
    }


def get_threat_intel_recency(
    db: Session = Depends(get_session), hours: int = 24
) -> Dict[str, Any]:
    """Compatibility name for the threat-intelligence recency query."""
    return get_recency(db=db, hours=hours)


def get_recency_summary(
    db: Session = Depends(get_session), hours: int = 24
) -> Dict[str, Any]:
    """Compatibility name for the threat-intelligence recency query."""
    return get_recency(db=db, hours=hours)


def get_threat_intel_recency_summary(
    db: Session = Depends(get_session), hours: int = 24
) -> Dict[str, Any]:
    """Compatibility name for the threat-intelligence recency query."""
    return get_recency(db=db, hours=hours)


def get_recency_data(
    db: Session = Depends(get_session), hours: int = 24
) -> Dict[str, Any]:
    """Compatibility name for the threat-intelligence recency query."""
    return get_recency(db=db, hours=hours)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    ThreatIntelRef.__table__.create(bind=engine)

    now = _utc_now_naive()
    seed_rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-stale",
            pulse_name="stale pulse",
            source="alpha",
            source_url="https://example.invalid/alpha/1",
            fetched_at=now - timedelta(days=8),
            is_aggregator=True,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.invalid",
            pulse_id="pulse-week",
            pulse_name="week pulse",
            source="alpha",
            source_url="https://example.invalid/alpha/2",
            fetched_at=now - timedelta(hours=2),
            is_aggregator=False,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-fresh",
            pulse_name="fresh pulse",
            source="beta",
            source_url="https://example.invalid/beta/1",
            fetched_at=now - timedelta(minutes=30),
            is_aggregator=False,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="beta.invalid",
            pulse_id="pulse-day",
            pulse_name="day pulse",
            source="beta",
            source_url="https://example.invalid/beta/2",
            fetched_at=now - timedelta(hours=3),
            is_aggregator=False,
        ),
    ]

    seed_session = TestingSession()
    seed_session.add_all(seed_rows)
    seed_session.commit()
    seed_session.close()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/recency")
    def recency(hours: int = 24, db: Session = Depends(get_session)):
        return get_recency(db=db, hours=hours)

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/recency", params={"hours": 24 * 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        alpha = next(row for row in payload["by_source"] if row["source"] == "alpha")
        assert alpha["stale"] == 1
        assert alpha["aggregator_count"] == 1

    engine.dispose()
    print("PASS")