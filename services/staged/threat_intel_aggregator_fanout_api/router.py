from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_threat_intel_fanout

router = APIRouter(prefix="/api")


class SourceCount(BaseModel):
    source: str
    count: int


class PulseFanout(BaseModel):
    pulse_id: str
    pulse_name: str
    indicator_count: int
    last_fetched: str


class ThreatIntelFanoutResponse(BaseModel):
    total: int
    aggregator_count: int
    direct_count: int
    by_indicator_type: dict[str, int]
    by_source: list[SourceCount]
    pulses: list[PulseFanout]
    stalest_fetch_hours: Optional[float]


@router.get(
    "/threat_intel/fanout",
    response_model=ThreatIntelFanoutResponse,
)
def threat_intel_fanout(
    days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
):
    return get_threat_intel_fanout(session=session, days=days)


if __name__ == "__main__":
    from datetime import datetime, timedelta

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

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=now - timedelta(days=2),
                    is_aggregator=True,
                    source="internal-aggregator",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now - timedelta(hours=1),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=now - timedelta(days=2),
                    is_aggregator=True,
                    source="internal-aggregator",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now - timedelta(hours=2),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.2",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=now - timedelta(days=1),
                    is_aggregator=False,
                    source="direct-feed",
                    source_url="https://example.test/pulse-2",
                    fetched_at=now - timedelta(hours=3),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://example.test/malware",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=now - timedelta(days=1),
                    is_aggregator=False,
                    source="direct-feed",
                    source_url="https://example.test/pulse-2",
                    fetched_at=now - timedelta(hours=4),
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

    with TestClient(test_app) as client:
        response = client.get("/api/threat_intel/fanout")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["aggregator_count"] == 2
        assert payload["direct_count"] == 2
        assert len(payload["pulses"]) == 2

    print("PASS")