from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import indicator_breadth

router = APIRouter(prefix="/api", tags=["indicator-pulse-breadth"])


class IndicatorTypeBreadth(BaseModel):
    indicator_type: str
    count: int
    distinct_pulses: int


class SourceBreadth(BaseModel):
    source: str
    aggregators: int


class IndicatorBreadthResponse(BaseModel):
    total_refs: int
    by_indicator_type: list[IndicatorTypeBreadth]
    by_source: list[SourceBreadth]
    stale_pulse_count: int


@router.get(
    "/threat-intel/indicator-breadth",
    response_model=IndicatorBreadthResponse,
)
def get_indicator_breadth(
    db: Session = Depends(get_session),
) -> IndicatorBreadthResponse:
    result: Any = indicator_breadth(db)
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()
    return IndicatorBreadthResponse(**result)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine)

    newest = datetime(2025, 1, 10, 12, 0, 0)
    rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse 1",
            pulse_created=newest - timedelta(days=1),
            is_aggregator=False,
            source="alpha",
            source_url="https://example.test/1",
            fetched_at=newest,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-2",
            pulse_name="Pulse 2",
            pulse_created=newest - timedelta(days=2),
            is_aggregator=True,
            source="alpha",
            source_url="https://example.test/2",
            fetched_at=newest - timedelta(days=1),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-3",
            pulse_name="Pulse 3",
            pulse_created=newest - timedelta(days=3),
            is_aggregator=False,
            source="bravo",
            source_url="https://example.test/3",
            fetched_at=newest - timedelta(days=2),
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="stale.test",
            pulse_id="pulse-4",
            pulse_name="Pulse 4",
            pulse_created=newest - timedelta(days=8),
            is_aggregator=False,
            source="bravo",
            source_url="https://example.test/4",
            fetched_at=newest - timedelta(days=8),
        ),
    ]

    with test_session_factory() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threat-intel/indicator-breadth"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_refs"] == 4
    assert len(payload["by_indicator_type"]) == 2
    assert payload["stale_pulse_count"] == 1
    print("PASS")