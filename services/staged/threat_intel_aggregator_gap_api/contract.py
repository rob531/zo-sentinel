from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Integer, String, and_, create_engine, func, or_, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class AggregatorSourceCount(BaseModel):
    source: str
    is_aggregator: bool
    count: int


class AggregatorGapResponse(BaseModel):
    total_refs: int
    aggregator_refs: int
    direct_refs: int
    distinct_indicator_types: int
    sources: list[AggregatorSourceCount]
    uncovered_indicator_types: list[str]
    latest_pulse_created: Optional[str]
    latest_fetched_at: Optional[str]


def _timestamp(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_aggregator_gap(session: Session = Depends(get_session)) -> AggregatorGapResponse:
    total_refs = session.scalar(select(func.count()).select_from(ThreatIntelRef)) or 0

    aggregator_refs = (
        session.scalar(
            select(func.count())
            .select_from(ThreatIntelRef)
            .where(ThreatIntelRef.is_aggregator.is_(True))
        )
        or 0
    )

    direct_refs = (
        session.scalar(
            select(func.count())
            .select_from(ThreatIntelRef)
            .where(
                or_(
                    ThreatIntelRef.is_aggregator.is_(False),
                    ThreatIntelRef.is_aggregator.is_(None),
                )
            )
        )
        or 0
    )

    distinct_indicator_types = (
        session.scalar(
            select(func.count(func.distinct(ThreatIntelRef.indicator_type))).select_from(
                ThreatIntelRef
            )
        )
        or 0
    )

    source_rows = session.execute(
        select(
            ThreatIntelRef.source,
            ThreatIntelRef.is_aggregator,
            func.count().label("count"),
        )
        .group_by(ThreatIntelRef.source, ThreatIntelRef.is_aggregator)
        .order_by(ThreatIntelRef.source, ThreatIntelRef.is_aggregator)
    ).all()

    sources = [
        AggregatorSourceCount(
            source=str(source) if source is not None else "unknown",
            is_aggregator=bool(is_aggregator),
            count=int(count),
        )
        for source, is_aggregator, count in source_rows
    ]

    all_types = set(
        session.scalars(
            select(ThreatIntelRef.indicator_type).where(
                ThreatIntelRef.indicator_type.is_not(None)
            )
        ).all()
    )
    non_aggregator_types = set(
        session.scalars(
            select(ThreatIntelRef.indicator_type).where(
                and_(
                    ThreatIntelRef.indicator_type.is_not(None),
                    or_(
                        ThreatIntelRef.is_aggregator.is_(False),
                        ThreatIntelRef.is_aggregator.is_(None),
                    ),
                )
            )
        ).all()
    )
    uncovered_indicator_types = sorted(
        str(indicator_type)
        for indicator_type in all_types - non_aggregator_types
    )

    latest_pulse_created = session.scalar(
        select(func.max(ThreatIntelRef.pulse_created))
    )
    latest_fetched_at = session.scalar(select(func.max(ThreatIntelRef.fetched_at)))

    return AggregatorGapResponse(
        total_refs=int(total_refs),
        aggregator_refs=int(aggregator_refs),
        direct_refs=int(direct_refs),
        distinct_indicator_types=int(distinct_indicator_types),
        sources=sources,
        uncovered_indicator_types=uncovered_indicator_types,
        latest_pulse_created=_timestamp(latest_pulse_created),
        latest_fetched_at=_timestamp(latest_fetched_at),
    )


app = FastAPI(title="Threat Intel Aggregator Gap API")


@app.get(
    "/api/threat-intel/aggregator-gap",
    response_model=AggregatorGapResponse,
)
def aggregator_gap(
    response: AggregatorGapResponse = Depends(get_aggregator_gap),
) -> AggregatorGapResponse:
    return response


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE threat_intel_refs (
                id INTEGER PRIMARY KEY,
                indicator_type VARCHAR,
                indicator_value VARCHAR,
                pulse_id VARCHAR,
                pulse_name VARCHAR,
                pulse_created VARCHAR,
                is_aggregator BOOLEAN,
                source VARCHAR,
                source_url VARCHAR,
                fetched_at VARCHAR
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO threat_intel_refs (
                id, indicator_type, indicator_value, pulse_id, pulse_name,
                pulse_created, is_aggregator, source, source_url, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "ip",
                    "192.0.2.1",
                    "pulse-1",
                    "Example Pulse One",
                    "2024-01-01T00:00:00",
                    True,
                    "aggregator-one",
                    "https://aggregator.example/one",
                    "2024-01-02T00:00:00",
                ),
                (
                    2,
                    "domain",
                    "example.org",
                    "pulse-2",
                    "Example Pulse Two",
                    "2024-01-02T00:00:00",
                    True,
                    "aggregator-two",
                    "https://aggregator.example/two",
                    "2024-01-03T00:00:00",
                ),
                (
                    3,
                    "ip",
                    "198.51.100.1",
                    "pulse-3",
                    "Direct Pulse One",
                    "2024-01-03T00:00:00",
                    False,
                    "direct-one",
                    "https://direct.example/one",
                    "2024-01-04T00:00:00",
                ),
                (
                    4,
                    "domain",
                    "example.net",
                    "pulse-4",
                    "Direct Pulse Two",
                    "2024-01-04T00:00:00",
                    False,
                    "direct-two",
                    "https://direct.example/two",
                    "2024-01-05T00:00:00",
                ),
            ],
        )

    def override_get_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/threat-intel/aggregator-gap")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_refs"] == 4
            assert payload["aggregator_refs"] == 2
            assert payload["direct_refs"] == 2
            assert payload["distinct_indicator_types"] == 2
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()