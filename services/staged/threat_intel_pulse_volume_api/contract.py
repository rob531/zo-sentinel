from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import ThreatIntelRef


class PulseAggregate(BaseModel):
    pulse_id: str
    pulse_name: str
    indicator_count: int
    distinct_indicator_types: int
    aggregator_count: int
    first_fetched_at: Optional[datetime] = None
    last_fetched_at: Optional[datetime] = None


class PulseVolumeSummary(BaseModel):
    total_indicators: int
    total_pulses: int
    aggregator_indicator_ratio: float


class PulseVolumeResponse(BaseModel):
    pulses: List[PulseAggregate]
    summary: PulseVolumeSummary


def get_pulse_volume(session: Session) -> Dict[str, Any]:
    pulse_stmt = (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.count(distinct(ThreatIntelRef.indicator_type)).label(
                "distinct_indicator_types"
            ),
            func.sum(
                case((ThreatIntelRef.is_aggregator == True, 1), else_=0)
            ).label("aggregator_count"),
            func.min(ThreatIntelRef.fetched_at).label("first_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
    )

    summary_stmt = select(
        func.count(ThreatIntelRef.id).label("total_indicators"),
        func.count(distinct(ThreatIntelRef.pulse_id)).label("total_pulses"),
        func.sum(
            case((ThreatIntelRef.is_aggregator == True, 1), else_=0)
        ).label("aggregator_count"),
    )

    pulse_rows = session.execute(pulse_stmt).mappings().all()
    summary_row = session.execute(summary_stmt).mappings().one()

    total_indicators = int(summary_row["total_indicators"] or 0)
    total_pulses = int(summary_row["total_pulses"] or 0)
    aggregator_count = int(summary_row["aggregator_count"] or 0)

    return {
        "pulses": [dict(row) for row in pulse_rows],
        "summary": {
            "total_indicators": total_indicators,
            "total_pulses": total_pulses,
            "aggregator_indicator_ratio": (
                float(aggregator_count) / total_indicators
                if total_indicators
                else 0.0
            ),
        },
    }


get_pulse_volume_data = get_pulse_volume
get_pulse_volume_payload = get_pulse_volume
build_pulse_volume = get_pulse_volume
fetch_pulse_volume = get_pulse_volume


router = APIRouter(prefix="/api")


@router.get("/threat-intel/pulses", response_model=PulseVolumeResponse)
def pulse_volume(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_pulse_volume(session)


def _seed_test_data(session: Session) -> None:
    table = ThreatIntelRef.__table__
    rows = [
        {
            "id": 1,
            "indicator_type": "ip",
            "indicator_value": "192.0.2.1",
            "pulse_id": "pulse-a",
            "pulse_name": "Pulse A",
            "pulse_created": datetime(2024, 1, 1),
            "is_aggregator": False,
            "source": "test",
            "source_url": "https://example.test/a",
            "fetched_at": datetime(2024, 1, 2, 10, 0),
        },
        {
            "id": 2,
            "indicator_type": "domain",
            "indicator_value": "example.test",
            "pulse_id": "pulse-a",
            "pulse_name": "Pulse A",
            "pulse_created": datetime(2024, 1, 1),
            "is_aggregator": True,
            "source": "test",
            "source_url": "https://example.test/a",
            "fetched_at": datetime(2024, 1, 3, 10, 0),
        },
        {
            "id": 3,
            "indicator_type": "ip",
            "indicator_value": "192.0.2.2",
            "pulse_id": "pulse-a",
            "pulse_name": "Pulse A",
            "pulse_created": datetime(2024, 1, 1),
            "is_aggregator": False,
            "source": "test",
            "source_url": "https://example.test/a",
            "fetched_at": datetime(2024, 1, 4, 10, 0),
        },
        {
            "id": 4,
            "indicator_type": "hash",
            "indicator_value": "abc123",
            "pulse_id": "pulse-b",
            "pulse_name": "Pulse B",
            "pulse_created": datetime(2024, 1, 1),
            "is_aggregator": False,
            "source": "test",
            "source_url": "https://example.test/b",
            "fetched_at": datetime(2024, 1, 5, 10, 0),
        },
        {
            "id": 5,
            "indicator_type": "url",
            "indicator_value": "https://example.test/path",
            "pulse_id": "pulse-b",
            "pulse_name": "Pulse B",
            "pulse_created": datetime(2024, 1, 1),
            "is_aggregator": False,
            "source": "test",
            "source_url": "https://example.test/b",
            "fetched_at": datetime(2024, 1, 6, 10, 0),
        },
    ]
    session.execute(table.insert(), rows)
    session.commit()


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with local_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with local_session() as session:
        _seed_test_data(session)

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulses")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_indicators"] == 5
    assert len(payload["pulses"]) == 2
    assert payload["pulses"][0]["indicator_count"] == 3

    engine.dispose()
    print("PASS")


run_contract = run_self_test
main = run_self_test


if __name__ == "__main__":
    main()