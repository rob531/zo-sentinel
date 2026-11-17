"""HTTP surface for threat pulse top indicators."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_top_pulses

router = APIRouter(prefix="/api", tags=["threat"])


class PulseSummary(BaseModel):
    pulse_id: int | None = None
    pulse_name: str | None = None
    indicator_count: int
    indicator_types: int
    has_aggregator: bool
    latest_fetched_at: datetime | None = None


class TopIndicatorsResponse(BaseModel):
    pulses: list[PulseSummary]
    total_pulses: int


@router.get("/threat/pulses/top", response_model=TopIndicatorsResponse)
def get_top_threat_pulses(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_session),
) -> TopIndicatorsResponse:
    result: Any = get_top_pulses(db, limit)

    if isinstance(result, dict):
        pulses = result.get("pulses", [])
        total_pulses = result.get("total_pulses", len(pulses))
    else:
        pulses = result
        total_pulses = len(pulses)

    return TopIndicatorsResponse(pulses=pulses, total_pulses=total_pulses)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import ThreatIntelRef

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id=1,
                    pulse_name="Critical Infrastructure",
                    source="seed",
                    source_url="https://example.com/1",
                    is_aggregator=True,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id=1,
                    pulse_name="Critical Infrastructure",
                    source="seed",
                    source_url="https://example.com/2",
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id=1,
                    pulse_name="Critical Infrastructure",
                    source="seed",
                    source_url="https://example.com/3",
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.1",
                    pulse_id=2,
                    pulse_name="Malware Campaigns",
                    source="seed",
                    source_url="https://example.com/4",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="malware.example",
                    pulse_id=2,
                    pulse_name="Malware Campaigns",
                    source="seed",
                    source_url="https://example.com/5",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat/pulses/top?limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["pulses"]) == 2
    busiest = next(pulse for pulse in payload["pulses"] if pulse["pulse_id"] == 1)
    assert busiest["indicator_count"] == 3
    assert busiest["has_aggregator"] is True
    print("PASS")