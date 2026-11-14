"""logic.py -- the service's data/computation layer (one concern, one file).

Provides a read-only summary of the contribution made by each threat-intel
source. The service uses the real application database session and ORM
models; the in-memory database below is used only by the __main__ self-test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_source_coverage(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Return source coverage and indicator-type counts for threat intel."""
    source_rows = db.execute(
        select(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id),
            func.count(ThreatIntelRef.pulse_id.distinct()),
            func.count(ThreatIntelRef.indicator_type.distinct()),
            func.max(ThreatIntelRef.fetched_at),
            func.min(ThreatIntelRef.fetched_at),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    sources = []
    for source, indicators, pulses, types, latest, oldest, aggregator_rows in source_rows:
        indicator_count = int(indicators or 0)
        aggregator_count = int(aggregator_rows or 0)
        sources.append(
            {
                "source": source,
                "indicators": indicator_count,
                "pulses": int(pulses or 0),
                "types": int(types or 0),
                "latest_fetched_at": latest,
                "oldest_fetched_at": oldest,
                "aggregator_ratio": (
                    float(aggregator_count) / indicator_count
                    if indicator_count
                    else 0.0
                ),
            }
        )

    type_rows = db.execute(
        select(ThreatIntelRef.indicator_type, func.count(ThreatIntelRef.id))
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.indicator_type.asc(),
        )
        .limit(10)
    ).all()

    indicator_types = [
        {"type": indicator_type, "count": int(count or 0)}
        for indicator_type, count in type_rows
    ]

    total_indicators = int(
        db.execute(
            select(func.count(ThreatIntelRef.id))
        ).scalar()
        or 0
    )

    return {
        "sources": sources,
        "indicator_types": indicator_types,
        "total_indicators": total_indicators,
    }


def source_coverage(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Compatibility alias for callers using the shorter function name."""
    return get_source_coverage(db)


if __name__ == "__main__":
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
    ThreatIntelRef.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-intel/source-coverage")
    def coverage_endpoint(db: Session = Depends(get_session)):
        return get_source_coverage(db)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Example pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-1",
                    pulse_name="Example pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://alpha.example/2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-2",
                    pulse_name="Other pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="a" * 64,
                    pulse_id="pulse-2",
                    pulse_name="Other pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="beta",
                    source_url="https://beta.example/2",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    response = TestClient(that_app).get("/api/threat-intel/source-coverage")
    payload = response.json()
    assert response.status_code == 200
    assert len(payload["sources"]) == 2
    assert payload["total_indicators"] == 4
    assert payload["indicator_types"][0]["count"] >= payload["indicator_types"][1]["count"]
    print("PASS")