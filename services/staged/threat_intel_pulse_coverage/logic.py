from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import Integer, case, cast, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_threat_intel_refs(session: Session = Depends(get_session)) -> List[ThreatIntelRef]:
    return list(session.query(ThreatIntelRef).all())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stale_hours(last_fetched_at: datetime) -> float:
    if last_fetched_at is None:
        return 0.0

    fetched_at = last_fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    else:
        fetched_at = fetched_at.astimezone(timezone.utc)

    return round(max(0.0, (_utc_now() - fetched_at).total_seconds() / 3600.0), 2)


def get_threat_intel_pulse_coverage(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    aggregate_flag = func.max(
        case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
    ).label("is_aggregator")

    rows = (
        session.query(
            func.min(ThreatIntelRef.pulse_id).label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label(
                "distinct_values"
            ),
            aggregate_flag,
            func.min(ThreatIntelRef.pulse_created).label("first_pulse_created"),
            func.max(ThreatIntelRef.pulse_created).label("last_pulse_created"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .group_by(ThreatIntelRef.pulse_name)
        .order_by(ThreatIntelRef.pulse_name, func.min(ThreatIntelRef.pulse_id))
        .all()
    )

    pulses: List[Dict[str, Any]] = []
    for row in rows:
        pulses.append(
            {
                "pulse_id": row.pulse_id,
                "pulse_name": row.pulse_name,
                "indicator_count": int(row.indicator_count or 0),
                "distinct_values": int(row.distinct_values or 0),
                "is_aggregator": bool(row.is_aggregator),
                "first_pulse_created": row.first_pulse_created,
                "last_pulse_created": row.last_pulse_created,
                "last_fetched_at": row.last_fetched_at,
                "stale_hours": _stale_hours(row.last_fetched_at),
            }
        )

    return {
        "total_pulses": len(pulses),
        "total_indicators": sum(pulse["indicator_count"] for pulse in pulses),
        "pulses": pulses,
    }


get_pulse_coverage = get_threat_intel_pulse_coverage
get_coverage = get_threat_intel_pulse_coverage
get_data = get_threat_intel_pulse_coverage
fetch_threat_intel_refs = get_threat_intel_refs


def coverage_endpoint(
    coverage: Dict[str, Any] = Depends(get_threat_intel_pulse_coverage),
) -> Dict[str, Any]:
    return coverage


def _self_test() -> None:
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
    test_session_factory = sessionmaker(bind=engine)

    with test_session_factory() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-aggregator",
                    pulse_name="Aggregator Pulse",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="seed",
                    source_url="https://example.test/1",
                    fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-aggregator",
                    pulse_name="Aggregator Pulse",
                    pulse_created=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.test/2",
                    fetched_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="198.51.100.1",
                    pulse_id="pulse-standard",
                    pulse_name="Standard Pulse",
                    pulse_created=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.test/3",
                    fetched_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/pulses/coverage")
    def coverage_route(
        coverage: Dict[str, Any] = Depends(get_threat_intel_pulse_coverage),
    ) -> Dict[str, Any]:
        return coverage

    response = TestClient(test_app).get("/api/threat-intel/pulses/coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_pulses"] == 2
    assert payload["total_indicators"] == 3
    aggregator = next(
        pulse for pulse in payload["pulses"] if pulse["pulse_name"] == "Aggregator Pulse"
    )
    assert aggregator["is_aggregator"] is True
    assert aggregator["indicator_count"] == 2
    print("PASS")


if __name__ == "__main__":
    _self_test()