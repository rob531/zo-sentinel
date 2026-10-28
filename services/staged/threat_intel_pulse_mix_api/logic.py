from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class IndicatorTypeBreakdown(BaseModel):
    indicator_type: str
    ref_count: int
    distinct_pulses: int


class PulseMixResponse(BaseModel):
    total_refs: int
    distinct_pulses: int
    aggregator_count: int
    latest_pulse_created: datetime | None
    by_indicator_type: list[IndicatorTypeBreakdown]


ThreatIntelPulseMixResponse = PulseMixResponse


def get_pulse_mix(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    total_refs = session.scalar(
        select(func.count()).select_from(ThreatIntelRef)
    ) or 0

    distinct_pulses = session.scalar(
        select(func.count(func.distinct(ThreatIntelRef.pulse_id)))
    ) or 0

    aggregator_count = session.scalar(
        select(
            func.count(
                func.distinct(
                    case(
                        (ThreatIntelRef.is_aggregator.is_(True), ThreatIntelRef.pulse_id),
                        else_=None,
                    )
                )
            )
        )
    ) or 0

    latest_pulse_created = session.scalar(
        select(func.max(ThreatIntelRef.pulse_created))
    )

    indicator_rows = session.execute(
        select(
            ThreatIntelRef.indicator_type,
            func.count().label("ref_count"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("distinct_pulses"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    return {
        "total_refs": int(total_refs),
        "distinct_pulses": int(distinct_pulses),
        "aggregator_count": int(aggregator_count),
        "latest_pulse_created": latest_pulse_created,
        "by_indicator_type": [
            {
                "indicator_type": indicator_type,
                "ref_count": int(ref_count),
                "distinct_pulses": int(pulse_count),
            }
            for indicator_type, ref_count, pulse_count in indicator_rows
        ],
    }


get_threat_intel_pulse_mix = get_pulse_mix
get_pulse_mix_breakdown = get_pulse_mix
compute_pulse_mix = get_pulse_mix


if __name__ == "__main__":
    from datetime import timedelta

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    base_time = datetime(2025, 1, 1, 12, 0, 0)
    rows = [
        ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=base_time,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/pulse-1",
            fetched_at=base_time,
        ),
        ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="192.0.2.2",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=base_time,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/pulse-1",
            fetched_at=base_time,
        ),
        ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="192.0.2.3",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=base_time + timedelta(days=1),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/pulse-2",
            fetched_at=base_time + timedelta(days=1),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="one.example.test",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=base_time,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/pulse-1",
            fetched_at=base_time,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="two.example.test",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=base_time + timedelta(days=1),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/pulse-2",
            fetched_at=base_time + timedelta(days=1),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="three.example.test",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=base_time + timedelta(days=1),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/pulse-2",
            fetched_at=base_time + timedelta(days=1),
        ),
    ]

    with TestSession() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get(
        "/api/threat-intel/pulse-mix",
        response_model=PulseMixResponse,
    )
    def pulse_mix(session: Session = Depends(get_session)):
        return get_pulse_mix(session)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulse-mix")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_refs"] == 6, payload
        assert len(payload["by_indicator_type"]) == 2, payload
        assert payload["distinct_pulses"] == 2, payload
        assert payload["aggregator_count"] == 1, payload
        assert payload["latest_pulse_created"] is not None, payload

    print("PASS")