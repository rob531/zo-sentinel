from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from app.db import get_session
from app.models import ThreatIntelRef


def _iso8601(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_pulse_coverage(session: Any, limit: int = 25) -> dict[str, Any]:
    rows = session.query(
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.pulse_name,
        ThreatIntelRef.pulse_created,
        ThreatIntelRef.is_aggregator,
        ThreatIntelRef.source,
        ThreatIntelRef.indicator_type,
    ).all()

    total_refs = len(rows)
    pulse_ids: set[Any] = set()
    sources: set[Any] = set()
    indicator_types: set[Any] = set()
    aggregator_refs = 0
    latest_pulse_created: Any = None

    grouped: dict[Any, dict[str, Any]] = {}

    for pulse_id, pulse_name, pulse_created, is_aggregator, source, indicator_type in rows:
        if pulse_id is not None:
            pulse_ids.add(pulse_id)
        if source is not None:
            sources.add(source)
        if indicator_type is not None:
            indicator_types.add(indicator_type)

        if bool(is_aggregator):
            aggregator_refs += 1

        if pulse_created is not None:
            if latest_pulse_created is None or pulse_created > latest_pulse_created:
                latest_pulse_created = pulse_created

        if pulse_id is None:
            continue

        pulse = grouped.setdefault(
            pulse_id,
            {
                "pulse_id": pulse_id,
                "pulse_name": pulse_name,
                "refs": 0,
                "created_at": _iso8601(pulse_created),
                "is_aggregator": False,
                "sources": set(),
            },
        )
        pulse["refs"] += 1
        pulse["is_aggregator"] = pulse["is_aggregator"] or bool(is_aggregator)
        if source is not None:
            pulse["sources"].add(source)
        if pulse["created_at"] is None and pulse_created is not None:
            pulse["created_at"] = _iso8601(pulse_created)

    pulses = []
    for pulse in grouped.values():
        pulses.append(
            {
                "pulse_id": pulse["pulse_id"],
                "pulse_name": pulse["pulse_name"],
                "refs": pulse["refs"],
                "created_at": pulse["created_at"],
                "is_aggregator": pulse["is_aggregator"],
                "sources": sorted(pulse["sources"], key=str),
            }
        )

    pulses.sort(
        key=lambda pulse: (
            -pulse["refs"],
            str(pulse["pulse_id"]),
            str(pulse["pulse_name"] or ""),
        )
    )
    pulses = pulses[: max(0, int(limit))]

    aggregator_pct = (
        (aggregator_refs / total_refs) * 100.0 if total_refs else 0.0
    )

    return {
        "total_refs": total_refs,
        "distinct_pulses": len(pulse_ids),
        "distinct_sources": len(sources),
        "distinct_indicator_types": len(indicator_types),
        "aggregator_refs": aggregator_refs,
        "aggregator_pct": aggregator_pct,
        "latest_pulse_created": _iso8601(latest_pulse_created),
        "pulses": pulses,
    }


def compute_pulse_coverage(session: Any, limit: int = 25) -> dict[str, Any]:
    return get_pulse_coverage(session, limit)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    pulse_id TEXT,
                    pulse_name TEXT,
                    pulse_created DATETIME,
                    is_aggregator BOOLEAN,
                    source TEXT,
                    indicator_type TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs (
                    pulse_id,
                    pulse_name,
                    pulse_created,
                    is_aggregator,
                    source,
                    indicator_type
                ) VALUES
                    ('pulse-a', 'Pulse A', '2024-01-01T00:00:00', 1, 'alpha', 'ip'),
                    ('pulse-a', 'Pulse A', '2024-01-01T00:00:00', 0, 'alpha', 'domain'),
                    ('pulse-a', 'Pulse A', '2024-01-01T00:00:00', 0, 'bravo', 'ip'),
                    ('pulse-b', 'Pulse B', '2024-01-02T00:00:00', 0, 'bravo', 'url')
                """
            )
        )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get("/api/threat-intel/pulse-coverage")
    def pulse_coverage(limit: int = 25, session=Depends(get_session)):
        return get_pulse_coverage(session, limit)

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get(
        "/api/threat-intel/pulse-coverage"
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "total_refs",
        "distinct_pulses",
        "distinct_sources",
        "distinct_indicator_types",
        "aggregator_refs",
        "aggregator_pct",
        "latest_pulse_created",
        "pulses",
    }
    assert payload["total_refs"] == 4
    assert payload["distinct_pulses"] == 2
    assert payload["aggregator_refs"] == 1
    assert len(payload["pulses"]) == 2
    print("PASS")