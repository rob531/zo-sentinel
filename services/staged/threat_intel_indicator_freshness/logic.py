from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return None


def get_indicator_freshness(
    db: Optional[Session] = None,
    days: int = 30,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    """Return freshness and distribution statistics for threat-intel indicators."""
    db = db if db is not None else session
    if db is None:
        raise ValueError("A database session is required")

    try:
        window_days = max(0, int(days))
    except (TypeError, ValueError):
        window_days = 30

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    refs = db.execute(select(ThreatIntelRef)).scalars().all()

    by_indicator_type: Counter[str] = Counter()
    pulse_counts: Counter[str] = Counter()
    aggregator_count = 0
    direct_count = 0
    fresh_pulse_count = 0
    stale_fetch_count = 0

    for ref in refs:
        indicator_type = ref.indicator_type or ""
        by_indicator_type[indicator_type] += 1

        if ref.is_aggregator:
            aggregator_count += 1
        else:
            direct_count += 1

        pulse_name = ref.pulse_name or ""
        pulse_counts[pulse_name] += 1

        pulse_created = _as_utc(ref.pulse_created)
        if pulse_created is not None and pulse_created >= cutoff:
            fresh_pulse_count += 1

        fetched_at = _as_utc(ref.fetched_at)
        if fetched_at is not None and fetched_at < cutoff:
            stale_fetch_count += 1

    top_pulses = [
        {"pulse_name": pulse_name, "count": count}
        for pulse_name, count in sorted(
            pulse_counts.items(), key=lambda item: (-item[1], item[0])
        )[:10]
    ]

    return {
        "days": window_days,
        "generated_at": now.isoformat(),
        "total": len(refs),
        "by_indicator_type": dict(sorted(by_indicator_type.items())),
        "aggregator_count": aggregator_count,
        "direct_count": direct_count,
        "fresh_pulse_count": fresh_pulse_count,
        "stale_fetch_count": stale_fetch_count,
        "top_pulses": top_pulses,
    }


def get_indicator_freshness_summary(
    db: Optional[Session] = None,
    days: int = 30,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return get_indicator_freshness(db, days, session=session)


def get_freshness_summary(
    db: Optional[Session] = None,
    days: int = 30,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return get_indicator_freshness(db, days, session=session)


def get_indicator_freshness_stats(
    db: Optional[Session] = None,
    days: int = 30,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return get_indicator_freshness(db, days, session=session)


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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=45)
    seed_rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id=1,
            pulse_name="Pulse Alpha",
            pulse_created=now,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id=1,
            pulse_name="Pulse Alpha",
            pulse_created=now,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id=2,
            pulse_name="Pulse Beta",
            pulse_created=now - timedelta(days=5),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.org",
            pulse_id=2,
            pulse_name="Pulse Beta",
            pulse_created=now - timedelta(days=45),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/4",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="a" * 64,
            pulse_id=3,
            pulse_name="Pulse Gamma",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/5",
            fetched_at=old,
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="b" * 64,
            pulse_id=3,
            pulse_name="Pulse Gamma",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/6",
            fetched_at=now,
        ),
    ]

    with TestSession() as test_session:
        test_session.add_all(seed_rows)
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/indicator-freshness")
    def indicator_freshness(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_indicator_freshness(session, days)

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get("/api/threat-intel/indicator-freshness?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 6
    assert payload["aggregator_count"] == 2
    assert payload["stale_fetch_count"] == 1
    assert sum(pulse["count"] for pulse in payload["top_pulses"]) == 6
    print("PASS")