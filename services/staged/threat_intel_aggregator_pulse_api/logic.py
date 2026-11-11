from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_aggregators(
    limit: int = 50,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return threat-intel pulses and their indicator/source summaries."""
    limit = max(1, min(int(limit), 200))

    total_indicators = (
        session.query(func.count(ThreatIntelRef.id))
        .scalar()
        or 0
    )

    pulse_rows = (
        session.query(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.min(ThreatIntelRef.fetched_at).label("first_fetched"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched"),
        )
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
        )
        .order_by(
            ThreatIntelRef.is_aggregator.desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
        .limit(limit)
        .all()
    )

    pulse_ids = [row.pulse_id for row in pulse_rows if row.pulse_id is not None]
    sources_by_pulse: Dict[Any, List[str]] = {}

    if pulse_ids:
        source_rows = (
            session.query(ThreatIntelRef.pulse_id, ThreatIntelRef.source)
            .filter(ThreatIntelRef.pulse_id.in_(pulse_ids))
            .filter(ThreatIntelRef.source.isnot(None))
            .distinct()
            .all()
        )
        for pulse_id, source in source_rows:
            sources_by_pulse.setdefault(pulse_id, []).append(str(source))
        for sources in sources_by_pulse.values():
            sources.sort()

    aggregators = []
    for row in pulse_rows:
        aggregators.append(
            {
                "pulse_id": row.pulse_id,
                "pulse_name": row.pulse_name,
                "is_aggregator": bool(row.is_aggregator),
                "indicator_count": int(row.indicator_count),
                "first_fetched": _isoformat(row.first_fetched),
                "last_fetched": _isoformat(row.last_fetched),
                "sources": sources_by_pulse.get(row.pulse_id, []),
            }
        )

    return {
        "aggregators": aggregators,
        "total_indicators": int(total_indicators),
    }


get_threat_intel_aggregators = get_aggregators
get_threat_intel_aggregator_pulses = get_aggregators


if __name__ == "__main__":
    from datetime import datetime

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
    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    seeded_rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-aggregated",
            pulse_name="Aggregated Threat Feed",
            pulse_created=datetime(2024, 1, 1, 0, 0, 0),
            is_aggregator=True,
            source="alpha-feed",
            source_url="https://alpha.example/1",
            fetched_at=datetime(2024, 1, 2, 0, 0, 0),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-aggregated",
            pulse_name="Aggregated Threat Feed",
            pulse_created=datetime(2024, 1, 1, 0, 0, 0),
            is_aggregator=True,
            source="beta-feed",
            source_url="https://beta.example/1",
            fetched_at=datetime(2024, 1, 3, 0, 0, 0),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-primary",
            pulse_name="Primary Threat Feed",
            pulse_created=datetime(2024, 1, 1, 0, 0, 0),
            is_aggregator=False,
            source="primary-feed",
            source_url="https://primary.example/1",
            fetched_at=datetime(2024, 1, 4, 0, 0, 0),
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="abc123",
            pulse_id="pulse-primary",
            pulse_name="Primary Threat Feed",
            pulse_created=datetime(2024, 1, 1, 0, 0, 0),
            is_aggregator=False,
            source="primary-feed",
            source_url="https://primary.example/2",
            fetched_at=datetime(2024, 1, 5, 0, 0, 0),
        ),
    ]

    db = TestingSessionLocal()
    try:
        db.add_all(seeded_rows)
        db.commit()
    finally:
        db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/aggregators")
    def read_aggregators(
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        return get_aggregators(limit=limit, session=session)

    client = TestClient(test_app)
    response = client.get("/api/threat-intel/aggregators")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["aggregators"]) == 2
    aggregated = next(
        pulse
        for pulse in payload["aggregators"]
        if pulse["pulse_id"] == "pulse-aggregated"
    )
    assert aggregated["indicator_count"] == 2
    assert aggregated["sources"]
    print("PASS")