"""HTTP surface for threat pulse coverage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_threat_pulses

router = APIRouter(prefix="/api", tags=["threat-pulse-coverage"])


class ThreatPulse(BaseModel):
    pulse_id: str
    pulse_name: str
    pulse_created: datetime | None = None
    indicator_count: int
    indicator_types: list[str]
    aggregator: bool
    age_hours: float


class ThreatPulseTotals(BaseModel):
    indicator_count: int
    distinct_pulses: int
    aggregator_count: int


class ThreatPulseCoverageResponse(BaseModel):
    pulses: list[ThreatPulse]
    totals: ThreatPulseTotals


@router.get("/threat/pulses", response_model=ThreatPulseCoverageResponse)
def get_pulse_coverage(
    db: Session = Depends(get_session),
) -> ThreatPulseCoverageResponse:
    result = get_threat_pulses(db)
    return ThreatPulseCoverageResponse.model_validate(result)


def _self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(bind=engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        db.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-alpha",
                    pulse_name="Alpha pulse",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=False,
                    source="contract-seed",
                    source_url="https://intel.example/alpha/1",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id="pulse-alpha",
                    pulse_name="Alpha pulse",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=True,
                    source="contract-seed",
                    source_url="https://intel.example/alpha/2",
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="a" * 64,
                    pulse_id="pulse-alpha",
                    pulse_name="Alpha pulse",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=False,
                    source="contract-seed",
                    source_url="https://intel.example/alpha/3",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.net",
                    pulse_id="pulse-beta",
                    pulse_name="Beta pulse",
                    pulse_created=now - timedelta(hours=5),
                    is_aggregator=False,
                    source="contract-seed",
                    source_url="https://intel.example/beta/1",
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://example.com/path",
                    pulse_id="pulse-beta",
                    pulse_name="Beta pulse",
                    pulse_created=now - timedelta(hours=5),
                    is_aggregator=False,
                    source="contract-seed",
                    source_url="https://intel.example/beta/2",
                ),
            ]
        )
        db.commit()

    def override_get_session() -> Iterator[Session]:
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/threat/pulses")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["pulses"]) == 2, payload
        assert payload["totals"]["indicator_count"] == 5, payload
        assert payload["totals"]["distinct_pulses"] == 2, payload
        assert payload["totals"]["aggregator_count"] == 1, payload
        assert sum(pulse["indicator_count"] for pulse in payload["pulses"]) == 5, payload
        assert sum(bool(pulse["aggregator"]) for pulse in payload["pulses"]) == 1, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()