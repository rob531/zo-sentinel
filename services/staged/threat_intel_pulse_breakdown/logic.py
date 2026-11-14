"""logic.py -- threat-intelligence pulse breakdown data and computation layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


STALE_AFTER = timedelta(hours=48)


def _utc_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime for portable stale comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_pulse_breakdown(db: Session) -> Dict[str, Any]:
    """Build a read-only breakdown of the current threat-intelligence pulses."""
    total_indicators = int(
        db.execute(select(func.count(ThreatIntelRef.id))).scalar() or 0
    )

    pulse_rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.is_aggregator,
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
        )
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.is_aggregator,
        )
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.pulse_name.asc(),
            ThreatIntelRef.pulse_id.asc(),
        )
    ).all()

    type_rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.indicator_type,
            func.count(ThreatIntelRef.id).label("count"),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.indicator_type)
        .order_by(
            ThreatIntelRef.pulse_id.asc(),
            ThreatIntelRef.indicator_type.asc(),
        )
    ).all()

    types_by_pulse: Dict[Any, list[Dict[str, Any]]] = {}
    for pulse_id, indicator_type, count in type_rows:
        types_by_pulse.setdefault(pulse_id, []).append(
            {
                "indicator_type": indicator_type,
                "count": int(count),
            }
        )

    pulses: list[Dict[str, Any]] = []
    aggregator_count = 0
    stale_pulses = 0
    cutoff = datetime.now(timezone.utc) - STALE_AFTER

    for pulse_id, pulse_name, pulse_created, is_aggregator, count, newest in pulse_rows:
        is_aggregator = bool(is_aggregator)
        if is_aggregator:
            aggregator_count += 1

        if newest is not None and _utc_datetime(newest) < cutoff:
            stale_pulses += 1

        pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": pulse_name,
                "pulse_created": pulse_created,
                "indicator_count": int(count),
                "types": types_by_pulse.get(pulse_id, []),
                "is_aggregator": is_aggregator,
                "newest_fetched_at": newest,
            }
        )

    return {
        "total_indicators": total_indicators,
        "aggregator_count": aggregator_count,
        "pulses": pulses,
        "stale_pulses": stale_pulses,
    }


# Compatibility aliases for routers using the service name explicitly.
get_threat_intel_pulse_breakdown = get_pulse_breakdown
get_threat_intel_pulses = get_pulse_breakdown


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    ThreatIntelRef.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-a",
                    pulse_name="Alpha",
                    pulse_created=now,
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/a",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-a",
                    pulse_name="Alpha",
                    pulse_created=now,
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/a",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-b",
                    pulse_name="Beta",
                    pulse_created=now,
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/b",
                    fetched_at=now - timedelta(hours=72),
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id="pulse-b",
                    pulse_name="Beta",
                    pulse_created=now,
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/b",
                    fetched_at=now - timedelta(hours=72),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/threat-intel/pulses")
    def read_pulses(db: Session = Depends(get_session)):
        return get_pulse_breakdown(db)

    with TestClient(local_app) as client:
        response = client.get("/api/threat-intel/pulses")
        assert response.status_code == 200
        body = response.json()
        assert body["total_indicators"] == 4
        assert len(body["pulses"]) == 2
        assert any(pulse["is_aggregator"] is True for pulse in body["pulses"])
        assert any(
            indicator_type["count"] == 2
            for pulse in body["pulses"]
            for indicator_type in pulse["types"]
        )

    print("PASS")