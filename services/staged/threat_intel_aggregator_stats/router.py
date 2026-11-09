"""HTTP surface for threat-intel aggregator statistics."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_aggregator_stats

router = APIRouter(prefix="/api/threat-intel", tags=["threat-intel"])


class SourceAggregatorStats(BaseModel):
    source: str
    total: int
    pulses: int
    indicator_types: int
    latest_pulse_created: Optional[datetime]
    latest_fetched_at: Optional[datetime]
    aggregator_rows: int
    non_aggregator_rows: int


class AggregatorStatsTotals(BaseModel):
    sources: int
    indicators: int
    stale: int


class AggregatorStatsResponse(BaseModel):
    sources: list[SourceAggregatorStats]
    totals: AggregatorStatsTotals


@router.get("/aggregator-stats", response_model=AggregatorStatsResponse)
def get_aggregator_stats_route(
    db: Session = Depends(get_session),
) -> AggregatorStatsResponse:
    return AggregatorStatsResponse(**get_aggregator_stats(db))


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    newest_fetched_at = datetime(2025, 1, 2, 12, 0, 0)
    stale_fetched_at = newest_fetched_at - timedelta(hours=26)

    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=datetime(2025, 1, 2, 10, 0, 0),
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://alpha.example/1",
                    fetched_at=stale_fetched_at,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="alpha.example",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=datetime(2025, 1, 2, 11, 0, 0),
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/2",
                    fetched_at=newest_fetched_at,
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="a" * 64,
                    pulse_id="pulse-3",
                    pulse_name="Pulse Three",
                    pulse_created=datetime(2025, 1, 2, 9, 0, 0),
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/1",
                    fetched_at=newest_fetched_at - timedelta(hours=1),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://beta.example/path",
                    pulse_id="pulse-4",
                    pulse_name="Pulse Four",
                    pulse_created=datetime(2025, 1, 2, 8, 0, 0),
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/2",
                    fetched_at=newest_fetched_at - timedelta(hours=2),
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/threat-intel/aggregator-stats")

    assert response.status_code == 200
    payload = response.json()
    sources = {item["source"]: item for item in payload["sources"]}
    assert sources["alpha"]["total"] == 2
    assert sources["beta"]["total"] == 2
    assert sources["alpha"]["aggregator_rows"] == 1
    assert payload["totals"] == {"sources": 2, "indicators": 4, "stale": 1}
    print("PASS")