"""logic.py -- threat pulse coverage computation over the real threat intelligence data."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_threat_pulses(db: Session) -> Dict[str, object]:
    """Return per-pulse threat indicator coverage and aggregate totals."""
    pulse_rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            func.max(ThreatIntelRef.pulse_created),
            func.max(ThreatIntelRef.is_aggregator),
            func.max(ThreatIntelRef.fetched_at),
            func.count(ThreatIntelRef.indicator_value),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
    ).all()

    type_rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.indicator_type,
        )
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.indicator_type,
        )
        .order_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.indicator_type,
        )
    ).all()

    types_by_pulse: Dict[tuple, List[str]] = {}
    for pulse_id, pulse_name, indicator_type in type_rows:
        key = (pulse_id, pulse_name)
        types_by_pulse.setdefault(key, []).append(indicator_type)

    now = datetime.now(timezone.utc)
    pulses: List[Dict[str, object]] = []
    total_indicators = 0
    aggregator_count = 0

    for pulse_id, pulse_name, pulse_created, is_aggregator, _fetched_at, count in pulse_rows:
        created = _as_utc(pulse_created)
        if created is None:
            age_hours: Optional[float] = None
        else:
            age_hours = round(max(0.0, (now - created).total_seconds() / 3600.0), 2)

        indicator_count = int(count or 0)
        total_indicators += indicator_count
        aggregator = bool(is_aggregator)
        if aggregator:
            aggregator_count += 1

        pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": pulse_name,
                "pulse_created": pulse_created,
                "indicator_count": indicator_count,
                "indicator_types": types_by_pulse.get((pulse_id, pulse_name), []),
                "aggregator": aggregator,
                "age_hours": age_hours,
            }
        )

    return {
        "pulses": pulses,
        "totals": {
            "indicator_count": total_indicators,
            "distinct_pulses": len(pulses),
            "aggregator_count": aggregator_count,
        },
    }


def get_threat_pulse_coverage(db: Session) -> Dict[str, object]:
    """Compatibility name for callers using the service's coverage terminology."""
    return get_threat_pulses(db)


def _self_test() -> None:
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
    SessionLocal = sessionmaker(bind=engine)

    created = datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=3)
    rows = [
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="one.example",
            pulse_id="pulse-a",
            pulse_name="Alpha Pulse",
            pulse_created=created,
            is_aggregator=False,
            source="seed",
            source_url="https://example.invalid/1",
            fetched_at=datetime.now(timezone.utc),
        ),
        ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="192.0.2.1",
            pulse_id="pulse-a",
            pulse_name="Alpha Pulse",
            pulse_created=created,
            is_aggregator=False,
            source="seed",
            source_url="https://example.invalid/2",
            fetched_at=datetime.now(timezone.utc),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="two.example",
            pulse_id="pulse-a",
            pulse_name="Alpha Pulse",
            pulse_created=created,
            is_aggregator=False,
            source="seed",
            source_url="https://example.invalid/3",
            fetched_at=datetime.now(timezone.utc),
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="abc123",
            pulse_id="pulse-b",
            pulse_name="Beta Pulse",
            pulse_created=created,
            is_aggregator=True,
            source="seed",
            source_url="https://example.invalid/4",
            fetched_at=datetime.now(timezone.utc),
        ),
        ThreatIntelRef(
            indicator_type="url",
            indicator_value="https://example.invalid/malware",
            pulse_id="pulse-b",
            pulse_name="Beta Pulse",
            pulse_created=created,
            is_aggregator=True,
            source="seed",
            source_url="https://example.invalid/5",
            fetched_at=datetime.now(timezone.utc),
        ),
    ]

    with SessionLocal() as session:
        session.add_all(rows)
        session.commit()

    local_app = FastAPI()

    @local_app.get("/api/threat/pulses")
    def threat_pulses(session: Session = Depends(get_session)):
        return get_threat_pulses(session)

    def override_session():
        with SessionLocal() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_session

    with TestClient(local_app) as client:
        response = client.get("/api/threat/pulses")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["pulses"]) == 2
        assert payload["totals"]["indicator_count"] == 5
        assert payload["totals"]["aggregator_count"] == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()