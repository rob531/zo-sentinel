from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_pulse_freshness


router = APIRouter(prefix="/api", tags=["threat-intel"])


class PulseFreshnessResponse(BaseModel):
    days: int
    total_refs: int
    distinct_pulses: int
    distinct_sources: int
    by_indicator_type: dict[str, int]
    by_source: dict[str, int]
    aggregator_count: int
    age_buckets: dict[str, int]
    last_fetched_at: Optional[datetime] = None
    stale_pulses: int


@router.get(
    "/threatintel/pulse-freshness",
    response_model=PulseFreshnessResponse,
)
def pulse_freshness(
    days: int = Query(default=7, ge=1),
    session: Session = Depends(get_session),
) -> PulseFreshnessResponse:
    return PulseFreshnessResponse(**get_pulse_freshness(session, days=days))


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(engine)

    now = datetime.utcnow()
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=45),
            is_aggregator=True,
            source="source-a",
            source_url="https://example.test/a",
            fetched_at=now - timedelta(hours=12),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=45),
            is_aggregator=False,
            source="source-a",
            source_url="https://example.test/a",
            fetched_at=now - timedelta(days=2),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=45),
            is_aggregator=False,
            source="source-a",
            source_url="https://example.test/a",
            fetched_at=now - timedelta(days=10),
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="abc123",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=3),
            is_aggregator=False,
            source="source-b",
            source_url="https://example.test/b",
            fetched_at=now - timedelta(days=20),
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="def456",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=3),
            is_aggregator=False,
            source="source-b",
            source_url="https://example.test/b",
            fetched_at=now - timedelta(days=40),
        ),
        ThreatIntelRef(
            indicator_type="url",
            indicator_value="https://example.test/path",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=3),
            is_aggregator=True,
            source="source-b",
            source_url="https://example.test/b",
            fetched_at=now - timedelta(days=5),
        ),
    ]

    with TestingSession() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session() -> Session:
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = __import__("fastapi").FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threatintel/pulse-freshness",
        params={"days": 7},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_refs"] == 6
    assert payload["distinct_pulses"] == 2
    assert payload["distinct_sources"] == 2
    assert payload["age_buckets"]["fresh_1d"] == 1
    assert payload["age_buckets"]["week_7d"] == 2
    assert payload["stale_pulses"] == 1
    print("PASS")


if __name__ == "__main__":
    _run_self_test()