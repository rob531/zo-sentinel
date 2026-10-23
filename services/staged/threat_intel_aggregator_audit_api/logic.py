from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi import Depends

from app.db import get_session
from app.models import ThreatIntelRef


def _timestamp_value(value: Any) -> Optional[Any]:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _group_breakdown(session: Session, is_aggregator: bool) -> Dict[str, Any]:
    filter_condition = (
        ThreatIntelRef.is_aggregator.is_(True)
        if is_aggregator
        else ThreatIntelRef.is_aggregator.is_(False)
    )

    count, source_count, pulse_count, earliest, latest = (
        session.query(
            func.count(ThreatIntelRef.id),
            func.count(func.distinct(ThreatIntelRef.source)),
            func.count(func.distinct(ThreatIntelRef.pulse_id)),
            func.min(ThreatIntelRef.pulse_created),
            func.max(ThreatIntelRef.pulse_created),
        )
        .filter(filter_condition)
        .one()
    )

    top_indicators = [
        {"indicator_value": indicator_value, "count": indicator_count}
        for indicator_value, indicator_count in (
            session.query(
                ThreatIntelRef.indicator_value,
                func.count(ThreatIntelRef.id).label("indicator_count"),
            )
            .filter(filter_condition)
            .group_by(ThreatIntelRef.indicator_value)
            .order_by(
                func.count(ThreatIntelRef.id).desc(),
                ThreatIntelRef.indicator_value.asc(),
            )
            .limit(15)
        )
    ]

    return {
        "count": int(count or 0),
        "sources": int(source_count or 0),
        "pulses": int(pulse_count or 0),
        "earliest_pulse_created": _timestamp_value(earliest),
        "latest_pulse_created": _timestamp_value(latest),
        "top_indicators": top_indicators,
    }


def get_aggregator_breakdown(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    total_indicators = session.query(func.count(ThreatIntelRef.id)).scalar() or 0

    sources = [
        {"source": source, "count": int(source_count)}
        for source, source_count in (
            session.query(
                ThreatIntelRef.source,
                func.count(ThreatIntelRef.id).label("source_count"),
            )
            .filter(ThreatIntelRef.source.isnot(None))
            .group_by(ThreatIntelRef.source)
            .order_by(ThreatIntelRef.source.asc())
        )
    ]

    return {
        "total_indicators": int(total_indicators),
        "aggregators": _group_breakdown(session, True),
        "non_aggregators": _group_breakdown(session, False),
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


get_aggregators = get_aggregator_breakdown
get_aggregator_stats = get_aggregator_breakdown
get_threat_intel_aggregators = get_aggregator_breakdown
get_threat_intel_aggregator_breakdown = get_aggregator_breakdown


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine)
    ThreatIntelRef.__table__.create(test_engine)

    with TestSession() as test_session:
        test_session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    fetched_at=datetime(2025, 1, 1, 0, 0, 0),
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    is_aggregator=True,
                    pulse_created=datetime(2025, 1, 1, 1, 0, 0),
                    pulse_id="pulse-1",
                    pulse_name="pulse-1",
                    source="alpha",
                    source_url="https://alpha.example/1",
                ),
                ThreatIntelRef(
                    id=2,
                    fetched_at=datetime(2025, 1, 2, 0, 0, 0),
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    is_aggregator=True,
                    pulse_created=datetime(2025, 1, 2, 1, 0, 0),
                    pulse_id="pulse-2",
                    pulse_name="pulse-2",
                    source="alpha",
                    source_url="https://alpha.example/2",
                ),
                ThreatIntelRef(
                    id=3,
                    fetched_at=datetime(2025, 1, 3, 0, 0, 0),
                    indicator_type="domain",
                    indicator_value="example.org",
                    is_aggregator=False,
                    pulse_created=datetime(2025, 1, 3, 1, 0, 0),
                    pulse_id="pulse-3",
                    pulse_name="pulse-3",
                    source="alpha",
                    source_url="https://alpha.example/3",
                ),
                ThreatIntelRef(
                    id=4,
                    fetched_at=datetime(2025, 1, 4, 0, 0, 0),
                    indicator_type="domain",
                    indicator_value="example.net",
                    is_aggregator=False,
                    pulse_created=datetime(2025, 1, 4, 1, 0, 0),
                    pulse_id="pulse-4",
                    pulse_name="pulse-4",
                    source="beta",
                    source_url="https://beta.example/4",
                ),
            ]
        )
        test_session.commit()

    that_app = FastAPI()

    @that_app.get("/api/threatintel/aggregators")
    def threatintel_aggregators(session: Session = Depends(get_session)):
        return get_aggregator_breakdown(session)

    that_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(that_app) as client:
        response = client.get("/api/threatintel/aggregators")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_indicators"] == 4
        assert payload["aggregators"]["count"] == 2
        assert payload["non_aggregators"]["count"] == 2
        assert len(payload["sources"]) == 2

    print("PASS")