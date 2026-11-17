"""HTTP surface for the threat intelligence pulse-source breakdown API."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_pulse_source_breakdown
except ImportError:
    from .logic import pulse_source_breakdown as get_pulse_source_breakdown

router = APIRouter(
    prefix="/api/threat-intel/pulse-sources",
    tags=["threat-intel"],
)


class PulseSourceBreakdown(BaseModel):
    source: str
    indicator_count: int
    distinct_pulses: int
    aggregator_indicators: int
    last_pulse_at: datetime | None
    staleness_seconds: int | None


class PulseSourceBreakdownResponse(BaseModel):
    total_indicators: int
    total_sources: int
    sources: list[PulseSourceBreakdown]


@router.get("", response_model=PulseSourceBreakdownResponse)
def get_pulse_sources(db: Session = Depends(get_session)) -> PulseSourceBreakdownResponse:
    breakdown = get_pulse_source_breakdown(db)

    if isinstance(breakdown, dict):
        raw_sources = breakdown.get("sources", [])
        total_indicators = breakdown.get(
            "total_indicators",
            sum(int(item["indicator_count"]) for item in raw_sources),
        )
        total_sources = breakdown.get("total_sources", len(raw_sources))
    else:
        raw_sources = breakdown
        total_indicators = sum(int(item["indicator_count"]) for item in raw_sources)
        total_sources = len(raw_sources)

    sources = [
        PulseSourceBreakdown.parse_obj(
            item if isinstance(item, dict) else dict(item)
        )
        for item in raw_sources
    ]

    return PulseSourceBreakdownResponse(
        total_indicators=int(total_indicators),
        total_sources=int(total_sources),
        sources=sources,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with local_session() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=False,
                    source="source-a",
                    source_url="https://example.test/a",
                    fetched_at=now - timedelta(minutes=5),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=now - timedelta(hours=1),
                    is_aggregator=True,
                    source="source-b",
                    source_url="https://example.test/b",
                    fetched_at=now - timedelta(minutes=3),
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=False,
                    source="source-c",
                    source_url="https://example.test/c",
                    fetched_at=now - timedelta(minutes=1),
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Any:
        with local_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat-intel/pulse-sources")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 3
    assert payload["total_indicators"] == 3
    assert payload["total_sources"] == 3
    assert any(
        source["aggregator_indicators"] == 1 for source in payload["sources"]
    )
    print("PASS")