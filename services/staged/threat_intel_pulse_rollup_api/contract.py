from collections import defaultdict
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class PulseRollup(BaseModel):
    pulse_id: str
    pulse_name: Optional[str]
    indicators: int
    indicator_types: int
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    sources: List[str]


class PulseRollupResponse(BaseModel):
    pulses: List[PulseRollup]
    total_pulses: int


def aggregate_pulses(session: Session) -> PulseRollupResponse:
    rows = session.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.source,
        )
        .where(ThreatIntelRef.pulse_id.is_not(None))
    ).all()

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.pulse_id].append(row)

    pulses = []
    for pulse_id, pulse_rows in grouped.items():
        created_values = [
            row.pulse_created for row in pulse_rows if row.pulse_created is not None
        ]
        indicator_types = {
            row.indicator_type
            for row in pulse_rows
            if row.indicator_type is not None
        }
        sources = {row.source for row in pulse_rows if row.source is not None}

        pulses.append(
            PulseRollup(
                pulse_id=str(pulse_id),
                pulse_name=(
                    str(pulse_rows[0].pulse_name)
                    if pulse_rows[0].pulse_name is not None
                    else None
                ),
                indicators=len(pulse_rows),
                indicator_types=len(indicator_types),
                first_seen=min(created_values) if created_values else None,
                last_seen=max(created_values) if created_values else None,
                sources=sorted(sources),
            )
        )

    pulses.sort(key=lambda pulse: (-pulse.indicators, pulse.pulse_id))
    return PulseRollupResponse(pulses=pulses, total_pulses=len(pulses))


def create_app() -> FastAPI:
    application = FastAPI(title="Threat Intel Pulse Rollup API")

    @application.get(
        "/api/threat-intel/pulses",
        response_model=PulseRollupResponse,
    )
    def list_threat_intel_pulses(
        session: Session = Depends(get_session),
    ) -> PulseRollupResponse:
        return aggregate_pulses(session)

    return application


app = create_app()


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type VARCHAR,
                    indicator_value VARCHAR,
                    pulse_id VARCHAR,
                    pulse_name VARCHAR,
                    pulse_created DATETIME,
                    is_aggregator BOOLEAN,
                    source VARCHAR,
                    source_url VARCHAR,
                    fetched_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs (
                    id,
                    indicator_type,
                    indicator_value,
                    pulse_id,
                    pulse_name,
                    pulse_created,
                    is_aggregator,
                    source,
                    source_url,
                    fetched_at
                ) VALUES (
                    :id,
                    :indicator_type,
                    :indicator_value,
                    :pulse_id,
                    :pulse_name,
                    :pulse_created,
                    :is_aggregator,
                    :source,
                    :source_url,
                    :fetched_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "indicator_type": "ipv4",
                    "indicator_value": "192.0.2.10",
                    "pulse_id": "pulse-a",
                    "pulse_name": "Pulse A",
                    "pulse_created": datetime(2024, 1, 1, 12, 0, 0),
                    "is_aggregator": False,
                    "source": "alpha",
                    "source_url": "https://example.test/a",
                    "fetched_at": datetime(2024, 1, 2, 12, 0, 0),
                },
                {
                    "id": 2,
                    "indicator_type": "domain",
                    "indicator_value": "example.test",
                    "pulse_id": "pulse-a",
                    "pulse_name": "Pulse A",
                    "pulse_created": datetime(2024, 1, 2, 12, 0, 0),
                    "is_aggregator": False,
                    "source": "beta",
                    "source_url": "https://example.test/b",
                    "fetched_at": datetime(2024, 1, 3, 12, 0, 0),
                },
                {
                    "id": 3,
                    "indicator_type": "ipv4",
                    "indicator_value": "198.51.100.20",
                    "pulse_id": "pulse-b",
                    "pulse_name": "Pulse B",
                    "pulse_created": datetime(2024, 2, 1, 12, 0, 0),
                    "is_aggregator": False,
                    "source": "gamma",
                    "source_url": "https://example.test/c",
                    "fetched_at": datetime(2024, 2, 2, 12, 0, 0),
                },
            ],
        )

    that_app = create_app()

    def override_get_session():
        session = Session(bind=engine, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/pulses")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_pulses"] == 2, payload

        largest_pulse = max(
            payload["pulses"], key=lambda pulse: pulse["indicators"]
        )
        assert largest_pulse["indicators"] == 2, payload
        assert largest_pulse["indicator_types"] == 2, payload

    print("PASS")


if __name__ == "__main__":
    run()