from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class IndicatorTypeEntry(BaseModel):
    indicator_type: str
    is_aggregator: bool
    rows: int
    distinct_pulses: int
    newest_fetched_at: str | None
    oldest_fetched_at: str | None


class SourceEntry(BaseModel):
    source: str
    rows: int


class IndicatorTypeResponse(BaseModel):
    generated_at: str
    indicator_types: list[IndicatorTypeEntry]
    sources: list[SourceEntry]
    observed_type_count: int
    total_rows: int


def _iso8601(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def build_indicator_type_breakdown(session: Session) -> IndicatorTypeResponse:
    statement = select(
        ThreatIntelRef.indicator_type,
        ThreatIntelRef.is_aggregator,
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.fetched_at,
        ThreatIntelRef.source,
    )
    rows = session.execute(statement).all()

    grouped_rows: Counter[tuple[str, bool]] = Counter()
    grouped_pulses: dict[tuple[str, bool], set[Any]] = defaultdict(set)
    grouped_timestamps: dict[tuple[str, bool], list[datetime | date]] = defaultdict(list)
    source_rows: Counter[str] = Counter()

    for indicator_type, is_aggregator, pulse_id, fetched_at, source in rows:
        type_name = "" if indicator_type is None else str(indicator_type)
        aggregator = bool(is_aggregator)
        key = (type_name, aggregator)

        grouped_rows[key] += 1
        if pulse_id is not None:
            grouped_pulses[key].add(pulse_id)
        if fetched_at is not None:
            grouped_timestamps[key].append(fetched_at)

        source_name = "" if source is None else str(source)
        source_rows[source_name] += 1

    indicator_types = []
    for indicator_type, is_aggregator in sorted(
        grouped_rows, key=lambda item: (item[0], item[1])
    ):
        key = (indicator_type, is_aggregator)
        timestamps = grouped_timestamps[key]
        newest = max(timestamps) if timestamps else None
        oldest = min(timestamps) if timestamps else None

        indicator_types.append(
            IndicatorTypeEntry(
                indicator_type=indicator_type,
                is_aggregator=is_aggregator,
                rows=grouped_rows[key],
                distinct_pulses=len(grouped_pulses[key]),
                newest_fetched_at=_iso8601(newest),
                oldest_fetched_at=_iso8601(oldest),
            )
        )

    sources = [
        SourceEntry(source=source, rows=count)
        for source, count in sorted(source_rows.items())
    ]

    return IndicatorTypeResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        indicator_types=indicator_types,
        sources=sources,
        observed_type_count=len({entry.indicator_type for entry in indicator_types}),
        total_rows=len(rows),
    )


def create_app() -> FastAPI:
    application = FastAPI(title="Threat Reference Indicator Type API")
    application.include_router(_router, prefix="/api")
    return application


from fastapi import APIRouter

_router = APIRouter()


@_router.get(
    "/threat/indicator-types",
    response_model=IndicatorTypeResponse,
)
def get_indicator_types(session: Session = Depends(get_session)) -> IndicatorTypeResponse:
    return build_indicator_type_breakdown(session)


app = create_app()


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()

    now = datetime.now(timezone.utc)
    session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id=101,
                pulse_name="Pulse Alpha",
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
                pulse_id=102,
                pulse_name="Pulse Beta",
                pulse_created=now,
                is_aggregator=False,
                source="alpha",
                source_url="https://example.test/2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id=103,
                pulse_name="Pulse Gamma",
                pulse_created=now,
                is_aggregator=True,
                source="beta",
                source_url="https://example.test/3",
                fetched_at=now,
            ),
        ]
    )
    session.commit()

    that_app = FastAPI()
    that_app.include_router(_router, prefix="/api")
    that_app.dependency_overrides[get_session] = lambda: session

    try:
        response = TestClient(that_app).get("/api/threat/indicator-types")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_rows"] == 3
        assert len(payload["indicator_types"]) == 2
        assert payload["observed_type_count"] == 2
        assert sorted(entry["rows"] for entry in payload["indicator_types"]) == [1, 2]
        assert any(
            source["source"] == "alpha" and source["rows"] == 2
            for source in payload["sources"]
        )
    finally:
        session.close()
        engine.dispose()


def main() -> None:
    run_contract()
    print("PASS")


if __name__ == "__main__":
    main()