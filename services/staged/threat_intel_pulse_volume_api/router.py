from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef
from .logic import *  # noqa: F401,F403

router = APIRouter(prefix="/api")


class PulseVolumeAggregate(BaseModel):
    pulse_id: str
    pulse_name: Optional[str]
    indicator_count: int
    distinct_indicator_types: int
    aggregator_count: int
    first_fetched_at: Optional[datetime]
    last_fetched_at: Optional[datetime]


class PulseVolumeSummary(BaseModel):
    total_indicators: int
    total_pulses: int
    aggregator_indicator_ratio: float


class PulseVolumeResponse(BaseModel):
    pulses: List[PulseVolumeAggregate]
    summary: PulseVolumeSummary


def get_pulse_volume(session: Session) -> PulseVolumeResponse:
    indicator_count = func.count(ThreatIntelRef.id)
    aggregator_count = func.sum(
        case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
    )

    rows = session.execute(
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            indicator_count.label("indicator_count"),
            func.count(func.distinct(ThreatIntelRef.indicator_type)).label(
                "distinct_indicator_types"
            ),
            aggregator_count.label("aggregator_count"),
            func.min(ThreatIntelRef.fetched_at).label("first_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .select_from(ThreatIntelRef)
        .where(ThreatIntelRef.pulse_id.is_not(None))
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(indicator_count.desc(), ThreatIntelRef.pulse_id.asc())
    ).all()

    total_indicators = session.execute(
        select(func.count(ThreatIntelRef.id)).select_from(ThreatIntelRef)
    ).scalar_one()
    total_pulses = session.execute(
        select(func.count(func.distinct(ThreatIntelRef.pulse_id))).select_from(
            ThreatIntelRef
        )
    ).scalar_one()
    total_aggregators = session.execute(
        select(
            func.coalesce(
                func.sum(
                    case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
                ),
                0,
            )
        ).select_from(ThreatIntelRef)
    ).scalar_one()

    pulses = [
        PulseVolumeAggregate(
            pulse_id=str(row.pulse_id),
            pulse_name=row.pulse_name,
            indicator_count=int(row.indicator_count or 0),
            distinct_indicator_types=int(row.distinct_indicator_types or 0),
            aggregator_count=int(row.aggregator_count or 0),
            first_fetched_at=row.first_fetched_at,
            last_fetched_at=row.last_fetched_at,
        )
        for row in rows
    ]

    return PulseVolumeResponse(
        pulses=pulses,
        summary=PulseVolumeSummary(
            total_indicators=int(total_indicators or 0),
            total_pulses=int(total_pulses or 0),
            aggregator_indicator_ratio=(
                float(total_aggregators or 0) / float(total_indicators)
                if total_indicators
                else 0.0
            ),
        ),
    )


@router.get("/threat-intel/pulses", response_model=PulseVolumeResponse)
def get_threat_intel_pulse_volume(
    session: Session = Depends(get_session),
) -> PulseVolumeResponse:
    return get_pulse_volume(session)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    ThreatIntelRef.__table__.create(test_engine)
    base_time = datetime(2025, 1, 1, 12, 0, 0)
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=base_time,
                    is_aggregator=True,
                    source="seed",
                    source_url="https://example.invalid/a",
                    fetched_at=base_time,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="one.example.invalid",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=base_time,
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.invalid/a",
                    fetched_at=base_time + timedelta(minutes=1),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://two.example.invalid",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=base_time,
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.invalid/a",
                    fetched_at=base_time + timedelta(minutes=2),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-b",
                    pulse_name="Pulse B",
                    pulse_created=base_time,
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.invalid/b",
                    fetched_at=base_time + timedelta(minutes=3),
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id="pulse-b",
                    pulse_name="Pulse B",
                    pulse_created=base_time,
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.invalid/b",
                    fetched_at=base_time + timedelta(minutes=4),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/threat-intel/pulses")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["total_indicators"] == 5, payload
    assert len(payload["pulses"]) == 2, payload
    assert payload["pulses"][0]["indicator_count"] == 3, payload
    assert payload["pulses"][0]["pulse_id"] == "pulse-a", payload

    print("PASS")