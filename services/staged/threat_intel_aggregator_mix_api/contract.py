from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import ThreatIntelRef


class SourceCount(BaseModel):
    source: str
    count: int


class MixGroup(BaseModel):
    count: int
    by_source: list[SourceCount]


class AggregatorMixResponse(BaseModel):
    total: int
    aggregator: MixGroup
    direct: MixGroup
    newest_fetched_at: Optional[datetime]


router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threat-intel/aggregator-mix",
    response_model=AggregatorMixResponse,
)
def get_aggregator_mix(
    session: Session = Depends(get_session),
) -> AggregatorMixResponse:
    aggregator_expression = cast(ThreatIntelRef.is_aggregator, Text)

    grouped_rows = session.execute(
        select(
            aggregator_expression.label("aggregator_flag"),
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id).label("row_count"),
        )
        .group_by(aggregator_expression, ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    newest_fetched_at = session.execute(
        select(func.max(ThreatIntelRef.fetched_at))
    ).scalar_one_or_none()

    aggregator_sources: dict[str, int] = {}
    direct_sources: dict[str, int] = {}
    total = 0

    for aggregator_flag, source, row_count in grouped_rows:
        count = int(row_count)
        total += count
        normalized_flag = str(aggregator_flag).strip().lower()
        normalized_source = str(source)

        if normalized_flag in {"true", "t", "1"}:
            aggregator_sources[normalized_source] = (
                aggregator_sources.get(normalized_source, 0) + count
            )
        else:
            direct_sources[normalized_source] = (
                direct_sources.get(normalized_source, 0) + count
            )

    def source_counts(values: dict[str, int]) -> list[SourceCount]:
        return [
            SourceCount(source=source, count=values[source])
            for source in sorted(values)
        ]

    return AggregatorMixResponse(
        total=total,
        aggregator=MixGroup(
            count=sum(aggregator_sources.values()),
            by_source=source_counts(aggregator_sources),
        ),
        direct=MixGroup(
            count=sum(direct_sources.values()),
            by_source=source_counts(direct_sources),
        ),
        newest_fetched_at=newest_fetched_at,
    )


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id TEXT PRIMARY KEY,
                    indicator_type TEXT NOT NULL,
                    indicator_value TEXT NOT NULL,
                    pulse_id TEXT,
                    pulse_name TEXT,
                    pulse_created TIMESTAMP,
                    is_aggregator BOOLEAN NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    fetched_at TIMESTAMP
                )
                """
            )
        )

    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    id_type_name = type(ThreatIntelRef.__table__.c.id.type).__name__.lower()
    if "uuid" in id_type_name:
        identifiers = [uuid4() for _ in range(5)]
    elif "int" in id_type_name:
        identifiers = list(range(1, 6))
    else:
        identifiers = [str(uuid4()) for _ in range(5)]

    fetched_at = datetime(2024, 1, 2, 3, 4, 5)
    pulse_created = datetime(2024, 1, 1, 12, 0, 0)
    rows = [
        ThreatIntelRef(
            id=identifiers[0],
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Example pulse one",
            pulse_created=pulse_created,
            is_aggregator=True,
            source="aggregator-alpha",
            source_url="https://alpha.example/pulse-1",
            fetched_at=fetched_at,
        ),
        ThreatIntelRef(
            id=identifiers[1],
            indicator_type="domain",
            indicator_value="example-one.test",
            pulse_id="pulse-1",
            pulse_name="Example pulse one",
            pulse_created=pulse_created,
            is_aggregator=True,
            source="aggregator-alpha",
            source_url="https://alpha.example/pulse-1",
            fetched_at=fetched_at,
        ),
        ThreatIntelRef(
            id=identifiers[2],
            indicator_type="url",
            indicator_value="https://example-two.test",
            pulse_id="pulse-2",
            pulse_name="Example pulse two",
            pulse_created=pulse_created,
            is_aggregator=True,
            source="aggregator-beta",
            source_url="https://beta.example/pulse-2",
            fetched_at=fetched_at,
        ),
        ThreatIntelRef(
            id=identifiers[3],
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-3",
            pulse_name="Direct pulse",
            pulse_created=pulse_created,
            is_aggregator=False,
            source="direct-alpha",
            source_url="https://direct.example/pulse-3",
            fetched_at=fetched_at,
        ),
        ThreatIntelRef(
            id=identifiers[4],
            indicator_type="hash",
            indicator_value="a" * 64,
            pulse_id="pulse-4",
            pulse_name="Another direct pulse",
            pulse_created=pulse_created,
            is_aggregator=False,
            source="direct-beta",
            source_url="https://direct.example/pulse-4",
            fetched_at=fetched_at,
        ),
    ]

    with SessionLocal() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/threat-intel/aggregator-mix")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert payload["aggregator"]["count"] == 3
        assert payload["direct"]["count"] == 2
        assert {
            "source": "aggregator-alpha",
            "count": 2,
        } in payload["aggregator"]["by_source"]
        assert {
            "source": "direct-alpha",
            "count": 1,
        } in payload["direct"]["by_source"]
        assert payload["newest_fetched_at"] is not None
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()