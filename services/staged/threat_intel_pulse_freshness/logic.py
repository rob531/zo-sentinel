from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_threat_intel_refs(session: Session) -> list[ThreatIntelRef]:
    return list(session.scalars(select(ThreatIntelRef)).all())


get_all_threat_intel_refs = get_threat_intel_refs


def _as_utc(value: Optional[Union[datetime, date]]) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_age_bucket(
    fetched_at: Optional[Union[datetime, date]],
    now: Optional[datetime] = None,
) -> str:
    timestamp = _as_utc(fetched_at)
    if timestamp is None:
        return "older_30d"
    current = _as_utc(now) or datetime.now(timezone.utc)
    age = current - timestamp
    if age < timedelta(days=1):
        return "fresh_1d"
    if age < timedelta(days=7):
        return "week_7d"
    if age < timedelta(days=30):
        return "month_30d"
    return "older_30d"


fetched_age_bucket = get_age_bucket


def get_pulse_freshness(
    session: Optional[Session] = None,
    days: Union[int, Session] = 30,
) -> dict:
    if isinstance(session, int) and isinstance(days, Session):
        session, days = days, session
    elif isinstance(days, Session):
        session, days = days, 30

    if session is None:
        raise ValueError("A database session is required")
    if not isinstance(days, int):
        raise TypeError("days must be an integer")
    if days < 0:
        raise ValueError("days must be non-negative")

    refs = get_threat_intel_refs(session)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    by_indicator_type: dict[str, int] = {}
    by_source: dict[str, int] = {}
    age_buckets = {
        "fresh_1d": 0,
        "week_7d": 0,
        "month_30d": 0,
        "older_30d": 0,
    }
    pulse_ids: set[str] = set()
    source_names: set[str] = set()
    stale_pulse_ids: set[str] = set()
    aggregator_count = 0
    newest_fetched_at: Optional[datetime] = None

    for ref in refs:
        pulse_ids.add(str(ref.pulse_id))
        source_names.add(ref.source)

        indicator_type = ref.indicator_type or "unknown"
        by_indicator_type[indicator_type] = by_indicator_type.get(indicator_type, 0) + 1

        source = ref.source or "unknown"
        by_source[source] = by_source.get(source, 0) + 1

        if ref.is_aggregator:
            aggregator_count += 1

        fetched_at = _as_utc(ref.fetched_at)
        if fetched_at is not None:
            age_buckets[get_age_bucket(fetched_at, now)] += 1
            if newest_fetched_at is None or fetched_at > newest_fetched_at:
                newest_fetched_at = fetched_at

        pulse_created = _as_utc(ref.pulse_created)
        if pulse_created is not None and pulse_created < cutoff:
            stale_pulse_ids.add(str(ref.pulse_id))

    return {
        "days": days,
        "total_refs": len(refs),
        "distinct_pulses": len(pulse_ids),
        "distinct_sources": len(source_names),
        "by_indicator_type": dict(sorted(by_indicator_type.items())),
        "by_source": dict(sorted(by_source.items())),
        "aggregator_count": aggregator_count,
        "age_buckets": age_buckets,
        "last_fetched_at": newest_fetched_at,
        "stale_pulses": len(stale_pulse_ids),
    }


get_pulse_freshness_stats = get_pulse_freshness
get_threat_intel_pulse_freshness = get_pulse_freshness


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id=1,
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=40),
            is_aggregator=False,
            source="alpha",
            source_url="https://alpha.example/1",
            fetched_at=now - timedelta(hours=12),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.org",
            pulse_id=1,
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=40),
            is_aggregator=True,
            source="alpha",
            source_url="https://alpha.example/2",
            fetched_at=now - timedelta(days=2),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id=1,
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=40),
            is_aggregator=False,
            source="alpha",
            source_url="https://alpha.example/3",
            fetched_at=now - timedelta(days=5),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.net",
            pulse_id=2,
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=5),
            is_aggregator=True,
            source="beta",
            source_url="https://beta.example/1",
            fetched_at=now - timedelta(days=10),
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="abc123",
            pulse_id=2,
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=5),
            is_aggregator=False,
            source="beta",
            source_url="https://beta.example/2",
            fetched_at=now - timedelta(days=20),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id=2,
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=5),
            is_aggregator=False,
            source="beta",
            source_url="https://beta.example/3",
            fetched_at=now - timedelta(days=40),
        ),
    ]

    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()

    that_app = FastAPI()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threatintel/pulse-freshness")
    def pulse_freshness(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_pulse_freshness(session=session, days=days)

    with TestClient(that_app) as client:
        response = client.get("/api/threatintel/pulse-freshness", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_refs"] == 6
        assert payload["distinct_pulses"] == 2
        assert payload["distinct_sources"] == 2
        assert payload["age_buckets"]["fresh_1d"] == 1
        assert payload["age_buckets"]["week_7d"] == 2
        assert payload["age_buckets"]["month_30d"] == 2
        assert payload["age_buckets"]["older_30d"] == 1
        assert payload["stale_pulses"] == 1

    print("PASS")