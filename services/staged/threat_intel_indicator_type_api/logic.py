from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def aggregate_indicator_types(
    session: Session,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    statement = select(
        ThreatIntelRef.indicator_type.label("indicator_type"),
        func.count().label("count"),
        func.count(func.distinct(ThreatIntelRef.pulse_id)).label("distinct_pulses"),
        func.sum(
            case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
        ).label("aggregator_count"),
        func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
    ).group_by(ThreatIntelRef.indicator_type)

    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)

    statement = statement.order_by(ThreatIntelRef.indicator_type)
    rows = session.execute(statement).mappings().all()

    return [
        {
            "indicator_type": row["indicator_type"],
            "count": int(row["count"] or 0),
            "distinct_pulses": int(row["distinct_pulses"] or 0),
            "aggregator_count": int(row["aggregator_count"] or 0),
            "last_fetched_at": row["last_fetched_at"],
        }
        for row in rows
    ]


def list_indicator_sources(
    session: Session,
    source: Optional[str] = None,
) -> List[str]:
    statement = select(ThreatIntelRef.source).where(ThreatIntelRef.source.is_not(None))

    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)

    statement = statement.distinct().order_by(ThreatIntelRef.source)
    return [row[0] for row in session.execute(statement).all()]


def build_indicator_types_response(
    session: Session,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    indicator_types = aggregate_indicator_types(session, source)
    return {
        "generated_at": datetime.now(timezone.utc),
        "total": len(indicator_types),
        "sources": list_indicator_sources(session, source),
        "indicator_types": indicator_types,
    }


def get_indicator_types(
    session: Session,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return aggregate_indicator_types(session, source)


def list_indicator_types(
    session: Session,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return aggregate_indicator_types(session, source)


def get_indicator_type_stats(
    session: Session,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return aggregate_indicator_types(session, source)


def get_indicator_type_aggregates(
    session: Session,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return aggregate_indicator_types(session, source)


def summarize_indicator_types(
    session: Session,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return build_indicator_types_response(session, source)


def get_indicator_type_summary(
    session: Session,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return build_indicator_types_response(session, source)


def get_indicator_types_response(
    session: Session,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return build_indicator_types_response(session, source)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse 1",
                    pulse_created=now,
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://example.test/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-2",
                    pulse_name="Pulse 2",
                    pulse_created=now,
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://example.test/2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-3",
                    pulse_name="Pulse 3",
                    pulse_created=now,
                    is_aggregator=False,
                    source="beta",
                    source_url="https://example.test/3",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id="pulse-3",
                    pulse_name="Pulse 3",
                    pulse_created=now,
                    is_aggregator=False,
                    source="beta",
                    source_url="https://example.test/4",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-intel/indicator-types")
    def indicator_types_endpoint(
        source: Optional[str] = None,
        session: Session = Depends(get_session),
    ):
        return build_indicator_types_response(session, source)

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/indicator-types")
        assert response.status_code == 200
        payload = response.json()
        by_type = {item["indicator_type"]: item for item in payload["indicator_types"]}
        assert set(by_type) == {"ip", "domain"}
        assert by_type["ip"]["count"] == 2
        assert by_type["ip"]["distinct_pulses"] == 2
        assert by_type["ip"]["aggregator_count"] == 1
        assert by_type["domain"]["count"] == 2
        assert by_type["domain"]["distinct_pulses"] == 1
        assert by_type["domain"]["aggregator_count"] == 0

        filtered = client.get(
            "/api/threat-intel/indicator-types",
            params={"source": "alpha"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["sources"] == ["alpha"]
        assert len(filtered_payload["indicator_types"]) == 1
        assert filtered_payload["indicator_types"][0]["indicator_type"] == "ip"
        assert filtered_payload["indicator_types"][0]["aggregator_count"] == 1

    print("PASS")