"""Logic for the threat-intelligence indicator catalogue API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import case, create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_threat_intel_indicators(
    db: Session,
    indicator_type: Optional[str] = None,
    include_aggregators: bool = False,
) -> Dict[str, Any]:
    """Return threat-intel coverage grouped by indicator type and source."""
    pulse_for_distinct = ThreatIntelRef.pulse_id
    if not include_aggregators:
        pulse_for_distinct = case(
            (ThreatIntelRef.is_aggregator.is_(False), ThreatIntelRef.pulse_id),
            else_=None,
        )

    type_query = select(
        ThreatIntelRef.indicator_type,
        func.count(ThreatIntelRef.id).label("total"),
        func.coalesce(
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ),
            0,
        ).label("aggregator_count"),
        func.count(func.distinct(pulse_for_distinct)).label("distinct_pulses"),
        func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
    )
    if indicator_type is not None:
        type_query = type_query.where(
            ThreatIntelRef.indicator_type == indicator_type
        )

    type_rows = db.execute(
        type_query.group_by(ThreatIntelRef.indicator_type)
    ).all()

    types = [
        {
            "indicator_type": str(row.indicator_type),
            "total": int(row.total),
            "aggregator_count": int(row.aggregator_count),
            "distinct_pulses": int(row.distinct_pulses),
            "latest_fetched_at": _isoformat(row.latest_fetched_at),
        }
        for row in type_rows
    ]
    types.sort(key=lambda item: item["indicator_type"])

    source_query = select(
        ThreatIntelRef.source,
        func.count(ThreatIntelRef.id).label("total"),
    )
    if indicator_type is not None:
        source_query = source_query.where(
            ThreatIntelRef.indicator_type == indicator_type
        )
    source_rows = db.execute(
        source_query.group_by(ThreatIntelRef.source)
    ).all()

    sources = [
        {
            "source": str(row.source),
            "total": int(row.total),
        }
        for row in source_rows
    ]
    sources.sort(key=lambda item: item["source"])

    return {
        "count": len(types),
        "types": types,
        "sources": sources,
    }


def _self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=engine)
    session_factory = create_engine  # Keep the production data layer authoritative.
    del session_factory

    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    test_app = FastAPI()

    @test_app.get("/api/threat-intel/indicators")
    def indicators(
        indicator_type: Optional[str] = Query(default=None),
        include_aggregators: bool = Query(default=False),
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_threat_intel_indicators(
            db,
            indicator_type=indicator_type,
            include_aggregators=include_aggregators,
        )

    def override_get_session():
        with SessionLocal() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as db:
        db.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=datetime(2024, 1, 1),
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://intel.example/1",
                    fetched_at=datetime(2024, 1, 2),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=datetime(2024, 1, 3),
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://intel.example/2",
                    fetched_at=datetime(2024, 1, 4),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-3",
                    pulse_name="Pulse Three",
                    pulse_created=datetime(2024, 1, 5),
                    is_aggregator=False,
                    source="beta",
                    source_url="https://intel.example/3",
                    fetched_at=datetime(2024, 1, 6),
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    pulse_id="pulse-4",
                    pulse_name="Pulse Four",
                    pulse_created=datetime(2024, 1, 7),
                    is_aggregator=True,
                    source="beta",
                    source_url="https://intel.example/4",
                    fetched_at=datetime(2024, 1, 8),
                ),
            ]
        )
        db.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/indicators")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert sum(item["total"] for item in payload["types"]) == 4
        assert any(
            item["aggregator_count"] >= 1 for item in payload["types"]
        )
        assert all(
            item["indicator_type"] for item in payload["types"]
        )

    print("PASS")


if __name__ == "__main__":
    _self_test()