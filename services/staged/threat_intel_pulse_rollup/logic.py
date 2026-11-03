from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class ThreatIntelPulse(BaseModel):
    pulse_id: int
    pulse_name: str | None
    indicator_count: int
    indicator_types: list[str]
    first_seen: datetime | None
    last_fetched: datetime
    is_aggregator: bool


class ThreatIntelPulseRollup(BaseModel):
    total_indicators: int
    distinct_pulses: int
    aggregator_pulses: int
    pulses: list[ThreatIntelPulse]


router = APIRouter(prefix="/api")


def aggregate_pulse_rollup(session: Session) -> ThreatIntelPulseRollup:
    grouped = session.execute(
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            func.max(ThreatIntelRef.pulse_name).label("pulse_name"),
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.min(ThreatIntelRef.pulse_created).label("first_seen"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched"),
            func.max(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ).label("is_aggregator"),
        ).group_by(ThreatIntelRef.pulse_id)
    ).all()

    type_rows = session.execute(
        select(ThreatIntelRef.pulse_id, ThreatIntelRef.indicator_type)
        .distinct(ThreatIntelRef.pulse_id, ThreatIntelRef.indicator_type)
    ).all()

    types_by_pulse: dict[Any, set[str]] = defaultdict(set)
    for pulse_id, indicator_type in type_rows:
        if indicator_type is not None:
            types_by_pulse[pulse_id].add(indicator_type)

    pulses = [
        ThreatIntelPulse(
            pulse_id=row.pulse_id,
            pulse_name=row.pulse_name,
            indicator_count=int(row.indicator_count),
            indicator_types=sorted(types_by_pulse[row.pulse_id]),
            first_seen=row.first_seen,
            last_fetched=row.last_fetched,
            is_aggregator=bool(row.is_aggregator),
        )
        for row in grouped
    ]
    pulses.sort(
        key=lambda pulse: (
            -pulse.indicator_count,
            pulse.pulse_name is None,
            pulse.pulse_name or "",
        )
    )

    return ThreatIntelPulseRollup(
        total_indicators=sum(pulse.indicator_count for pulse in pulses),
        distinct_pulses=len(pulses),
        aggregator_pulses=sum(1 for pulse in pulses if pulse.is_aggregator),
        pulses=pulses,
    )


def get_pulse_rollup(
    session: Session = Depends(get_session),
) -> ThreatIntelPulseRollup:
    return aggregate_pulse_rollup(session)


@router.get(
    "/threat-intel/pulses",
    response_model=ThreatIntelPulseRollup,
)
def list_threat_intel_pulses(
    session: Session = Depends(get_session),
) -> ThreatIntelPulseRollup:
    return aggregate_pulse_rollup(session)


def self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    ThreatIntelRef.__table__.create(engine)

    session = session_factory()
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id=100,
                    pulse_name="aggregator-pulse",
                    pulse_created=datetime(2025, 1, 1, 0, 0, 0),
                    is_aggregator=True,
                    source="self-test",
                    source_url="https://example.test/1",
                    fetched_at=datetime(2025, 1, 2, 0, 0, 0),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id=100,
                    pulse_name="aggregator-pulse",
                    pulse_created=datetime(2025, 1, 1, 0, 0, 0),
                    is_aggregator=True,
                    source="self-test",
                    source_url="https://example.test/2",
                    fetched_at=datetime(2025, 1, 3, 0, 0, 0),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id=100,
                    pulse_name="aggregator-pulse",
                    pulse_created=datetime(2025, 1, 1, 0, 0, 0),
                    is_aggregator=True,
                    source="self-test",
                    source_url="https://example.test/3",
                    fetched_at=datetime(2025, 1, 4, 0, 0, 0),
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="ipv4",
                    indicator_value="198.51.100.1",
                    pulse_id=200,
                    pulse_name="normal-pulse",
                    pulse_created=datetime(2025, 2, 1, 0, 0, 0),
                    is_aggregator=False,
                    source="self-test",
                    source_url="https://example.test/4",
                    fetched_at=datetime(2025, 2, 2, 0, 0, 0),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        override_session = session_factory()
        try:
            yield override_session
        finally:
            override_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/threat-intel/pulses")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_indicators"] == 4
            assert payload["distinct_pulses"] == 2
            assert payload["aggregator_pulses"] == 1
            assert payload["pulses"][0]["indicator_count"] == 3
            assert payload["pulses"][0]["is_aggregator"] is True
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    self_test()