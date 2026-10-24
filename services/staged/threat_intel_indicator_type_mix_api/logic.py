from collections.abc import Generator
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import case, create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


def get_indicator_type_mix(
    session: Session = Depends(get_session),
) -> dict:
    rows = session.execute(
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("count"),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ).label("aggregator_count"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    types = [
        {
            "indicator_type": row.indicator_type,
            "count": int(row.count or 0),
            "aggregator_count": int(row.aggregator_count or 0),
            "unique_pulses": int(row.count or 0),
        }
        for row in rows
    ]

    return {
        "total": sum(bucket["count"] for bucket in types),
        "types": types,
    }


get_indicator_type_mix_logic = get_indicator_type_mix
get_threat_intel_indicator_type_mix = get_indicator_type_mix


if __name__ == "__main__":
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)

    def test_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    is_aggregator=True,
                    pulse_created=now,
                    pulse_id="pulse-1",
                    pulse_name="Test Pulse 1",
                    source="test",
                    source_url="https://example.com/pulse-1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    is_aggregator=False,
                    pulse_created=now,
                    pulse_id="pulse-2",
                    pulse_name="Test Pulse 2",
                    source="test",
                    source_url="https://example.com/pulse-2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.com",
                    is_aggregator=False,
                    pulse_created=now,
                    pulse_id="pulse-3",
                    pulse_name="Test Pulse 3",
                    source="test",
                    source_url="https://example.com/pulse-3",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/threat-intel/indicator-types")
    def indicator_types(payload: dict = Depends(get_indicator_type_mix)) -> dict:
        return payload

    that_app.dependency_overrides[get_session] = test_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/indicator-types")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert any(
            bucket["indicator_type"] == "ip" and bucket["count"] == 2
            for bucket in payload["types"]
        )

    print("PASS")