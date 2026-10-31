from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from fastapi import Depends, FastAPI, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

__all__ = [
    "aggregate_aggregator_pulses",
    "build_aggregator_pulses_response",
    "get_aggregator_pulses",
]


def _validated_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer between 1 and 500") from exc
    if value < 1 or value > 500:
        raise ValueError("limit must be between 1 and 500")
    return value


def aggregate_aggregator_pulses(
    session: Session,
    source: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    limit = _validated_limit(limit)

    conditions = [ThreatIntelRef.is_aggregator.is_(True)]
    if source is not None:
        conditions.append(ThreatIntelRef.source == source)

    grouped_stmt = (
        select(
            ThreatIntelRef.source.label("source"),
            ThreatIntelRef.pulse_id.label("pulse_id"),
            func.max(ThreatIntelRef.pulse_name).label("pulse_name"),
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.count(distinct(ThreatIntelRef.indicator_type)).label(
                "indicator_type_count"
            ),
            func.min(ThreatIntelRef.pulse_created).label("first_pulse_created"),
            func.max(ThreatIntelRef.pulse_created).label("last_pulse_created"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .where(*conditions)
        .group_by(ThreatIntelRef.source, ThreatIntelRef.pulse_id)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.source.asc(),
            ThreatIntelRef.pulse_id.asc(),
        )
        .limit(limit)
    )

    grouped_rows = session.execute(grouped_stmt).all()

    type_stmt = (
        select(
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.indicator_type,
        )
        .where(*conditions)
        .distinct()
    )

    types_by_pulse: dict[tuple[Any, Any], set[str]] = defaultdict(set)
    for row_source, row_pulse_id, indicator_type in session.execute(type_stmt):
        if indicator_type is not None:
            types_by_pulse[(row_source, row_pulse_id)].add(str(indicator_type))

    pulses = [
        {
            "source": row.source,
            "pulse_id": row.pulse_id,
            "pulse_name": row.pulse_name,
            "indicator_count": int(row.indicator_count),
            "indicator_types": sorted(types_by_pulse[(row.source, row.pulse_id)]),
            "first_pulse_created": row.first_pulse_created,
            "last_pulse_created": row.last_pulse_created,
            "last_fetched_at": row.last_fetched_at,
        }
        for row in grouped_rows
    ]

    return {
        "total_pulses": len(pulses),
        "pulses": pulses,
    }


def get_aggregator_pulses(
    session: Session,
    source: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    return aggregate_aggregator_pulses(session, source=source, limit=limit)


def build_aggregator_pulses_response(
    session: Session,
    source: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    return aggregate_aggregator_pulses(session, source=source, limit=limit)


def _self_test() -> None:
    from datetime import datetime, timezone

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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="example",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="example",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="example",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-intel/aggregator-pulses")
    def aggregator_pulses(
        source: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        session: Session = Depends(get_session),
    ):
        return get_aggregator_pulses(session, source=source, limit=limit)

    response = TestClient(that_app).get(
        "/api/threat-intel/aggregator-pulses",
        params={"source": "example"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_pulses"] == 1, payload
    assert len(payload["pulses"]) == 1, payload
    assert payload["pulses"][0]["indicator_count"] == 2, payload
    assert payload["pulses"][0]["indicator_types"] == ["domain", "ipv4"], payload
    print("PASS")


if __name__ == "__main__":
    _self_test()