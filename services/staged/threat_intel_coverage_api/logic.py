"""Data and computation layer for threat intelligence coverage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_coverage(db: Session) -> Dict[str, Any]:
    """Return threat-intelligence coverage grouped by indicator type and source."""
    total = int(db.execute(select(func.count()).select_from(ThreatIntelRef)).scalar_one())

    indicator_rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            func.count(),
            func.count(func.distinct(ThreatIntelRef.indicator_value)),
            func.max(ThreatIntelRef.pulse_created),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    by_indicator_type = []
    for indicator_type, count, distinct_values, newest_pulse_created, aggregator_count in indicator_rows:
        count = int(count or 0)
        aggregator_count = int(aggregator_count or 0)
        by_indicator_type.append(
            {
                "indicator_type": indicator_type or "unknown",
                "count": count,
                "distinct_values": int(distinct_values or 0),
                "newest_pulse_created": newest_pulse_created,
                "aggregator_ratio": aggregator_count / count if count else 0.0,
            }
        )

    source_rows = db.execute(
        select(ThreatIntelRef.source, func.count())
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    by_source = [
        {"source": source or "unknown", "count": int(count or 0)}
        for source, count in source_rows
    ]

    return {
        "total": total,
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_threat_intel_coverage(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers using the service-specific function name."""
    return get_coverage(db)


if __name__ == "__main__":
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
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="feed-a",
                    source_url="https://feed-a.example/1",
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="feed-a",
                    source_url="https://feed-a.example/2",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-3",
                    pulse_name="Pulse Three",
                    pulse_created=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="feed-b",
                    source_url="https://feed-b.example/1",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-4",
                    pulse_name="Pulse Four",
                    pulse_created=datetime(2024, 1, 4, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="feed-b",
                    source_url="https://feed-b.example/2",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="other.test",
                    pulse_id="pulse-5",
                    pulse_name="Pulse Five",
                    pulse_created=datetime(2024, 1, 5, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="feed-b",
                    source_url="https://feed-b.example/3",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/coverage")
    def coverage(db: Session = Depends(get_session)):
        return get_coverage(db)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert len(payload["by_indicator_type"]) == 2
        assert sum(row["count"] for row in payload["by_source"]) == 5

    print("PASS")