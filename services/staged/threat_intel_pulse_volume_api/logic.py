from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_pulse_volume(session: Session = Depends(get_session)) -> dict[str, Any]:
    pulse_rows = session.execute(
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.count(distinct(ThreatIntelRef.indicator_type)).label(
                "distinct_indicator_types"
            ),
            func.coalesce(
                func.sum(
                    case(
                        (ThreatIntelRef.is_aggregator.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("aggregator_count"),
            func.min(ThreatIntelRef.fetched_at).label("first_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
    ).mappings()

    pulses = [dict(row) for row in pulse_rows]

    summary_row = session.execute(
        select(
            func.count(ThreatIntelRef.id).label("total_indicators"),
            func.count(distinct(ThreatIntelRef.pulse_id)).label("total_pulses"),
            func.coalesce(
                func.sum(
                    case(
                        (ThreatIntelRef.is_aggregator.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("aggregator_indicator_count"),
        )
    ).mappings().one()

    total_indicators = int(summary_row["total_indicators"] or 0)
    aggregator_indicator_count = int(
        summary_row["aggregator_indicator_count"] or 0
    )

    summary = {
        "total_indicators": total_indicators,
        "total_pulses": int(summary_row["total_pulses"] or 0),
        "aggregator_indicator_ratio": (
            aggregator_indicator_count / total_indicators
            if total_indicators
            else 0.0
        ),
    }

    return {"pulses": pulses, "summary": summary}


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    rows = [
        {
            "id": 1,
            "indicator_type": "ip",
            "indicator_value": "192.0.2.1",
            "pulse_id": "pulse-1",
            "pulse_name": "Pulse One",
            "pulse_created": datetime(2025, 1, 1),
            "is_aggregator": True,
            "source": "source-a",
            "source_url": "https://example.com/1",
            "fetched_at": datetime(2025, 1, 2),
        },
        {
            "id": 2,
            "indicator_type": "domain",
            "indicator_value": "example.org",
            "pulse_id": "pulse-1",
            "pulse_name": "Pulse One",
            "pulse_created": datetime(2025, 1, 1),
            "is_aggregator": False,
            "source": "source-a",
            "source_url": "https://example.com/2",
            "fetched_at": datetime(2025, 1, 3),
        },
        {
            "id": 3,
            "indicator_type": "domain",
            "indicator_value": "example.net",
            "pulse_id": "pulse-1",
            "pulse_name": "Pulse One",
            "pulse_created": datetime(2025, 1, 1),
            "is_aggregator": False,
            "source": "source-b",
            "source_url": "https://example.com/3",
            "fetched_at": datetime(2025, 1, 4),
        },
        {
            "id": 4,
            "indicator_type": "ip",
            "indicator_value": "198.51.100.1",
            "pulse_id": "pulse-2",
            "pulse_name": "Pulse Two",
            "pulse_created": datetime(2025, 1, 5),
            "is_aggregator": False,
            "source": "source-b",
            "source_url": "https://example.com/4",
            "fetched_at": datetime(2025, 1, 6),
        },
        {
            "id": 5,
            "indicator_type": "url",
            "indicator_value": "https://example.net/path",
            "pulse_id": "pulse-2",
            "pulse_name": "Pulse Two",
            "pulse_created": datetime(2025, 1, 5),
            "is_aggregator": False,
            "source": "source-c",
            "source_url": "https://example.com/5",
            "fetched_at": datetime(2025, 1, 7),
        },
    ]
    testing_session.execute(ThreatIntelRef.__table__.insert(), rows)
    testing_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/pulses")
    def threat_intel_pulses(session: Session = Depends(get_session)):
        return get_pulse_volume(session)

    test_app.dependency_overrides[get_session] = lambda: testing_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulses")
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["total_indicators"] == 5
        assert payload["summary"]["total_pulses"] == 2
        assert len(payload["pulses"]) == 2
        assert payload["pulses"][0]["indicator_count"] == 3
        assert payload["pulses"][0]["aggregator_count"] == 1
        assert payload["pulses"][0]["distinct_indicator_types"] == 2
        assert payload["summary"]["aggregator_indicator_ratio"] == 0.2

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()