from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class ThreatIntelPulseIndicatorCountResponse(BaseModel):
    pulse_id: str
    pulse_name: str
    indicator_count: int
    aggregator_count: int
    distinct_indicator_types: int
    newest_indicator_at: Optional[datetime]


class ThreatIntelPulseIndicatorCountsResponse(BaseModel):
    pulses: List[ThreatIntelPulseIndicatorCountResponse]
    total_pulses: int
    total_indicators: int


def _group_pulse_rows(rows) -> List[Dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.pulse_id].append(row)

    pulses = []
    for pulse_id in sorted(grouped):
        pulse_rows = grouped[pulse_id]
        indicator_values = {
            row.indicator_value
            for row in pulse_rows
            if row.indicator_value is not None
        }
        aggregator_values = {
            row.indicator_value
            for row in pulse_rows
            if row.is_aggregator and row.indicator_value is not None
        }
        indicator_types = {
            row.indicator_type
            for row in pulse_rows
            if row.indicator_type is not None
        }
        timestamps = [
            row.pulse_created
            for row in pulse_rows
            if row.pulse_created is not None
        ]
        if not timestamps:
            timestamps = [
                row.fetched_at
                for row in pulse_rows
                if row.fetched_at is not None
            ]

        pulse_name = next(
            (row.pulse_name for row in pulse_rows if row.pulse_name is not None),
            "",
        )
        pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": pulse_name,
                "indicator_count": len(indicator_values),
                "aggregator_count": len(aggregator_values),
                "distinct_indicator_types": len(indicator_types),
                "newest_indicator_at": max(timestamps) if timestamps else None,
            }
        )
    return pulses


def get_threat_intel_pulse_indicator_counts(
    db: Session = Depends(get_session),
) -> Dict:
    rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.fetched_at,
        )
    ).all()

    pulses = _group_pulse_rows(rows)
    return {
        "pulses": pulses,
        "total_pulses": len(pulses),
        "total_indicators": sum(pulse["indicator_count"] for pulse in pulses),
    }


get_pulse_indicator_counts = get_threat_intel_pulse_indicator_counts


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    base_time = datetime(2025, 1, 1, 12, 0, 0)
    seed_rows = [
        ThreatIntelRef(
            id=1,
            pulse_id="pulse-alpha",
            pulse_name="Alpha Pulse",
            indicator_type="domain",
            indicator_value="alpha.example",
            is_aggregator=False,
            source="feed-a",
            source_url="https://feed-a.example/alpha",
            pulse_created=base_time,
            fetched_at=base_time + timedelta(minutes=1),
        ),
        ThreatIntelRef(
            id=2,
            pulse_id="pulse-alpha",
            pulse_name="Alpha Pulse",
            indicator_type="url",
            indicator_value="https://malicious.example/alpha",
            is_aggregator=True,
            source="aggregator-a",
            source_url="https://aggregator-a.example/alpha",
            pulse_created=base_time + timedelta(hours=1),
            fetched_at=base_time + timedelta(hours=1, minutes=1),
        ),
        ThreatIntelRef(
            id=3,
            pulse_id="pulse-alpha",
            pulse_name="Alpha Pulse",
            indicator_type="domain",
            indicator_value="alpha-two.example",
            is_aggregator=False,
            source="feed-b",
            source_url="https://feed-b.example/alpha",
            pulse_created=base_time + timedelta(hours=2),
            fetched_at=base_time + timedelta(hours=2, minutes=1),
        ),
        ThreatIntelRef(
            id=4,
            pulse_id="pulse-beta",
            pulse_name="Beta Pulse",
            indicator_type="ip",
            indicator_value="192.0.2.10",
            is_aggregator=False,
            source="feed-c",
            source_url="https://feed-c.example/beta",
            pulse_created=base_time + timedelta(hours=3),
            fetched_at=base_time + timedelta(hours=3, minutes=1),
        ),
        ThreatIntelRef(
            id=5,
            pulse_id="pulse-beta",
            pulse_name="Beta Pulse",
            indicator_type="domain",
            indicator_value="beta.example",
            is_aggregator=False,
            source="feed-d",
            source_url="https://feed-d.example/beta",
            pulse_created=base_time + timedelta(hours=4),
            fetched_at=base_time + timedelta(hours=4, minutes=1),
        ),
        ThreatIntelRef(
            id=6,
            pulse_id="pulse-beta",
            pulse_name="Beta Pulse",
            indicator_type="ip",
            indicator_value="192.0.2.11",
            is_aggregator=False,
            source="feed-e",
            source_url="https://feed-e.example/beta",
            pulse_created=base_time + timedelta(hours=5),
            fetched_at=base_time + timedelta(hours=5, minutes=1),
        ),
    ]

    with TestSession() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.get(
        "/api/threat-intel/pulse-indicator-counts",
        response_model=ThreatIntelPulseIndicatorCountsResponse,
    )(get_threat_intel_pulse_indicator_counts)

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulse-indicator-counts")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_pulses"] == 2
        assert payload["total_indicators"] == len(seed_rows)
        assert all(
            pulse["distinct_indicator_types"] >= 1
            for pulse in payload["pulses"]
        )
        alpha = next(
            pulse for pulse in payload["pulses"] if pulse["pulse_id"] == "pulse-alpha"
        )
        assert alpha["aggregator_count"] == 1

    print("PASS")