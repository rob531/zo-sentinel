from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _indicator_row(row: ThreatIntelRef) -> dict[str, Any]:
    return {
        "id": row.id,
        "indicator_type": row.indicator_type,
        "indicator_value": row.indicator_value,
        "pulse_id": row.pulse_id,
        "pulse_name": row.pulse_name,
        "pulse_created": row.pulse_created,
        "is_aggregator": row.is_aggregator,
        "source": row.source,
        "source_url": row.source_url,
        "fetched_at": row.fetched_at,
    }


def list_indicators(
    db: Session = Depends(get_session),
    indicator_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    effective_limit = max(1, min(int(limit), 500))

    count_statement = select(func.count()).select_from(ThreatIntelRef)
    statement = select(ThreatIntelRef)

    if indicator_type:
        count_statement = count_statement.where(
            ThreatIntelRef.indicator_type == indicator_type
        )
        statement = statement.where(
            ThreatIntelRef.indicator_type == indicator_type
        )

    total = db.execute(count_statement).scalar_one()
    rows = (
        db.execute(
            statement.order_by(ThreatIntelRef.fetched_at.desc()).limit(effective_limit)
        )
        .scalars()
        .all()
    )

    return {
        "total": int(total),
        "limit": effective_limit,
        "indicators": [_indicator_row(row) for row in rows],
    }


# Compatibility names for callers using the common service naming conventions.
lookup_indicators = list_indicators
query_indicators = list_indicators
get_indicators = list_indicators
fetch_indicators = list_indicators
get_threat_intel_indicators = list_indicators
list_threat_intel_indicators = list_indicators


if __name__ == "__main__":
    from datetime import datetime, timedelta

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_session = TestSession()
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    test_session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=base_time,
                is_aggregator=True,
                source="example",
                source_url="https://example.test/pulse-1",
                fetched_at=base_time,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=base_time + timedelta(minutes=1),
                is_aggregator=False,
                source="example",
                source_url="https://example.test/pulse-2",
                fetched_at=base_time + timedelta(minutes=1),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-3",
                pulse_name="Pulse Three",
                pulse_created=base_time + timedelta(minutes=2),
                is_aggregator=False,
                source="example",
                source_url="https://example.test/pulse-3",
                fetched_at=base_time + timedelta(minutes=2),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="sample.test",
                pulse_id="pulse-4",
                pulse_name="Pulse Four",
                pulse_created=base_time + timedelta(minutes=3),
                is_aggregator=True,
                source="example",
                source_url="https://example.test/pulse-4",
                fetched_at=base_time + timedelta(minutes=3),
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/indicators")
    def test_indicators(
        indicator_type: str | None = None,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return list_indicators(
            db=db,
            indicator_type=indicator_type,
            limit=limit,
        )

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/indicators")
        assert response.status_code == 200
        assert len(response.json()["indicators"]) == 4

        response = client.get(
            "/api/threat-intel/indicators",
            params={"indicator_type": "ip"},
        )
        assert response.status_code == 200
        assert len(response.json()["indicators"]) == 2
        assert all(
            item["indicator_type"] == "ip"
            for item in response.json()["indicators"]
        )

        response = client.get(
            "/api/threat-intel/indicators",
            params={"limit": 1},
        )
        assert response.status_code == 200
        assert len(response.json()["indicators"]) == 1
        assert response.json()["limit"] == 1

    test_session.close()
    test_engine.dispose()
    print("PASS")