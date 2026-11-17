"""logic.py -- threat pulse top indicators data and computation layer."""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import Integer, bindparam, cast, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_top_pulses(db: Session, limit: int = 10) -> Dict[str, Any]:
    """Return the busiest threat-intel pulse groups and the total group count."""
    bounded_limit = max(1, min(int(limit), 100))

    grouped = (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            func.count(ThreatIntelRef.indicator_value).label("indicator_count"),
            func.count(func.distinct(ThreatIntelRef.indicator_type)).label(
                "indicator_types"
            ),
            func.max(cast(ThreatIntelRef.is_aggregator, Integer)).label(
                "has_aggregator"
            ),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .subquery()
    )

    total_pulses = int(
        db.execute(select(func.count()).select_from(grouped)).scalar_one()
    )

    statement = (
        select(
            grouped.c.pulse_id,
            grouped.c.pulse_name,
            grouped.c.indicator_count,
            grouped.c.indicator_types,
            grouped.c.has_aggregator,
            grouped.c.latest_fetched_at,
        )
        .order_by(
            grouped.c.indicator_count.desc(),
            grouped.c.pulse_id.asc(),
        )
        .limit(bindparam("limit"))
    )
    rows = db.execute(statement, {"limit": bounded_limit}).all()

    pulses = [
        {
            "pulse_id": row.pulse_id,
            "pulse_name": row.pulse_name,
            "indicator_count": int(row.indicator_count),
            "indicator_types": int(row.indicator_types),
            "has_aggregator": bool(row.has_aggregator),
            "latest_fetched_at": row.latest_fetched_at,
        }
        for row in rows
    ]
    return {"pulses": pulses, "total_pulses": total_pulses}


def get_top_indicators(db: Session, limit: int = 10) -> Dict[str, Any]:
    """Compatibility alias for callers using the indicators terminology."""
    return get_top_pulses(db, limit)


def get_top_threat_pulses(db: Session, limit: int = 10) -> Dict[str, Any]:
    """Compatibility alias for callers using the threat-pulse terminology."""
    return get_top_pulses(db, limit)


def _self_test() -> None:
    from fastapi import Depends, FastAPI
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/1",
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/2",
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="ip",
                    indicator_value="192.0.2.3",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/3",
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="hash",
                    indicator_value="abc",
                    pulse_id="pulse-b",
                    pulse_name="Pulse B",
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/4",
                ),
                ThreatIntelRef(
                    id=5,
                    indicator_type="hash",
                    indicator_value="def",
                    pulse_id="pulse-b",
                    pulse_name="Pulse B",
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/5",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/threat/pulses/top")
    def top_pulses(
        limit: int = 10,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_top_pulses(db, limit)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    with TestClient(test_app) as client:
        response = client.get("/api/threat/pulses/top?limit=10")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["pulses"]) == 2
        assert payload["total_pulses"] == 2
        assert payload["pulses"][0]["indicator_count"] == 3
        assert payload["pulses"][0]["has_aggregator"] is True

    print("PASS")


if __name__ == "__main__":
    _self_test()