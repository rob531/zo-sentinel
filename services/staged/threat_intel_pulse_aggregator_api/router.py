from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef
from .logic import get_threat_intel_pulses


router = APIRouter()


class ThreatIntelPulseAggregate(BaseModel):
    pulse_id: str
    pulse_name: Optional[str] = None
    indicator_count: int
    type_count: int
    value_count: int
    is_aggregator: bool
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    source_count: int


class ThreatIntelPulseTotals(BaseModel):
    pulse_count: int
    indicator_count: int


class ThreatIntelPulsesResponse(BaseModel):
    pulses: list[ThreatIntelPulseAggregate]
    totals: ThreatIntelPulseTotals


@router.get(
    "/threat-intel/pulses",
    response_model=ThreatIntelPulsesResponse,
)
def threat_intel_pulses(
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    return get_threat_intel_pulses(session, limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type VARCHAR NOT NULL,
                    indicator_value VARCHAR NOT NULL,
                    pulse_id VARCHAR,
                    pulse_name VARCHAR,
                    pulse_created DATETIME,
                    is_aggregator BOOLEAN NOT NULL DEFAULT 0,
                    source VARCHAR NOT NULL,
                    source_url VARCHAR NOT NULL,
                    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                ) VALUES
                    (1, 'ipv4', '192.0.2.1', 'pulse-a', 'Aggregate Pulse',
                     '2024-01-01T00:00:00', 1, 'source-a',
                     'https://example.test/a', '2024-01-01T00:00:00'),
                    (2, 'ipv4', '192.0.2.1', 'pulse-a', 'Aggregate Pulse',
                     '2024-01-02T00:00:00', 1, 'source-a',
                     'https://example.test/b', '2024-01-02T00:00:00'),
                    (3, 'domain', 'example.test', 'pulse-a', 'Aggregate Pulse',
                     '2024-01-03T00:00:00', 1, 'source-b',
                     'https://example.test/c', '2024-01-03T00:00:00'),
                    (4, 'ipv4', '198.51.100.1', 'pulse-b', 'Second Pulse',
                     '2024-01-04T00:00:00', 0, 'source-c',
                     'https://example.test/d', '2024-01-04T00:00:00'),
                    (5, 'ipv4', '203.0.113.1', NULL, 'Excluded Pulse',
                     '2024-01-05T00:00:00', 0, 'source-d',
                     'https://example.test/e', '2024-01-05T00:00:00')
                """
            )
        )

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat-intel/pulses")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert len(payload["pulses"]) == 2, payload
    aggregate = next(
        pulse for pulse in payload["pulses"] if pulse["pulse_id"] == "pulse-a"
    )
    assert aggregate["indicator_count"] == 3, payload
    assert aggregate["value_count"] == 2, payload
    assert aggregate["is_aggregator"] is True, payload
    assert payload["totals"]["pulse_count"] == 2, payload

    print("PASS")