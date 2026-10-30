from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_threat_intel_pulse_span(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = session.query(ThreatIntelRef).all()

    grouped: dict[str, list[ThreatIntelRef]] = defaultdict(list)
    for row in rows:
        if row.pulse_id is not None:
            grouped[str(row.pulse_id)].append(row)

    pulses: list[dict[str, Any]] = []
    distinct_indicators: set[str] = set()
    aggregator_pulses: set[str] = set()

    for row in rows:
        if row.indicator_value is not None:
            distinct_indicators.add(str(row.indicator_value))

    for pulse_id, pulse_rows in sorted(grouped.items()):
        indicator_types = Counter(
            str(row.indicator_type)
            for row in pulse_rows
            if row.indicator_type is not None
        )
        fetched_times = [
            row.fetched_at
            for row in pulse_rows
            if row.fetched_at is not None
        ]
        pulse_created_values = [
            row.pulse_created
            for row in pulse_rows
            if row.pulse_created is not None
        ]
        is_aggregator = any(bool(row.is_aggregator) for row in pulse_rows)

        if is_aggregator:
            aggregator_pulses.add(pulse_id)

        pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": next(
                    (
                        row.pulse_name
                        for row in pulse_rows
                        if row.pulse_name is not None
                    ),
                    None,
                ),
                "indicator_count": len(pulse_rows),
                "is_aggregator": is_aggregator,
                "pulse_created": (
                    min(pulse_created_values)
                    if pulse_created_values
                    else None
                ),
                "oldest_fetched_at": (
                    min(fetched_times) if fetched_times else None
                ),
                "newest_fetched_at": (
                    max(fetched_times) if fetched_times else None
                ),
                "indicator_types": dict(sorted(indicator_types.items())),
            }
        )

    return {
        "pulses": pulses,
        "totals": {
            "distinct_pulses": len(grouped),
            "distinct_indicators": len(distinct_indicators),
            "aggregator_pulses": len(aggregator_pulses),
        },
    }


get_pulse_span = get_threat_intel_pulse_span


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        test_session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-aggregator",
                    pulse_name="Aggregator Pulse",
                    pulse_created=datetime(2024, 1, 1, 12, 0, 0),
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/1",
                    fetched_at=datetime(2024, 1, 2, 8, 0, 0),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id="pulse-aggregator",
                    pulse_name="Aggregator Pulse",
                    pulse_created=datetime(2024, 1, 1, 12, 0, 0),
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/2",
                    fetched_at=datetime(2024, 1, 2, 9, 0, 0),
                ),
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="198.51.100.2",
                    pulse_id="pulse-direct",
                    pulse_name="Direct Pulse",
                    pulse_created=datetime(2024, 2, 1, 12, 0, 0),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/3",
                    fetched_at=datetime(2024, 2, 2, 8, 0, 0),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://example.test/malware",
                    pulse_id="pulse-direct",
                    pulse_name="Direct Pulse",
                    pulse_created=datetime(2024, 2, 1, 12, 0, 0),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/4",
                    fetched_at=datetime(2024, 2, 2, 9, 0, 0),
                ),
            ]
        )
        test_session.commit()

    that_app = FastAPI()

    def override_get_session() -> Session:
        with TestSession() as session:
            return session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-intel/pulse-span")
    def pulse_span(session: Session = Depends(get_session)) -> dict[str, Any]:
        return get_threat_intel_pulse_span(session)

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/pulse-span")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["distinct_pulses"] == 2

        aggregator = next(
            pulse
            for pulse in payload["pulses"]
            if pulse["pulse_id"] == "pulse-aggregator"
        )
        assert aggregator["indicator_count"] == 2
        assert sum(aggregator["indicator_types"].values()) == (
            aggregator["indicator_count"]
        )

    print("PASS")