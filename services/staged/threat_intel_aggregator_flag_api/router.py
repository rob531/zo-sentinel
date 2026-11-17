from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_aggregator_breakdown

router = APIRouter(prefix="/api", tags=["threat-intel"])


class AggregatorBreakdownResponse(BaseModel):
    indicator_type: str
    total_indicators: int
    aggregator_indicators: int
    aggregator_ratio: float
    distinct_pulses: int
    latest_pulse_created: datetime | None


@router.get(
    "/threat-intel/aggregators",
    response_model=list[AggregatorBreakdownResponse],
)
def list_aggregator_breakdown(
    indicator_type: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> list[AggregatorBreakdownResponse]:
    rows: list[dict[str, Any]] = get_aggregator_breakdown(
        session=session,
        indicator_type=indicator_type,
        days=days,
    )
    return [AggregatorBreakdownResponse.model_validate(row) for row in rows]


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="alpha-one.example",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=2),
            is_aggregator=True,
            source="test",
            source_url="https://example.test/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="alpha-two.example",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=2),
            is_aggregator=True,
            source="test",
            source_url="https://example.test/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=3),
            is_aggregator=True,
            source="test",
            source_url="https://example.test/3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=3),
            is_aggregator=True,
            source="test",
            source_url="https://example.test/4",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/5",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.4",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/6",
            fetched_at=now,
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = lambda: session

        from fastapi.testclient import TestClient

        response = TestClient(that_app).get(
            "/api/threat-intel/aggregators",
            params={"days": 3650},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_type = {row["indicator_type"]: row for row in payload}
        assert by_type["domain"]["aggregator_ratio"] == 1.0
        assert by_type["ip"]["aggregator_ratio"] == 0.5

    print("PASS")


if __name__ == "__main__":
    _self_test()