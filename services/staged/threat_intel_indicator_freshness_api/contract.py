from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import ThreatIntelRef


class IndicatorTypeFreshness(BaseModel):
    indicator_type: str
    total: int
    aggregators: int
    stale_count: int
    oldest_fetched_at: Optional[str]
    newest_fetched_at: Optional[str]


class SourceCount(BaseModel):
    source: str
    count: int


class FreshnessTotals(BaseModel):
    indicators: int
    stale: int


class IndicatorFreshnessResponse(BaseModel):
    days: int
    types: List[IndicatorTypeFreshness]
    sources: List[SourceCount]
    totals: FreshnessTotals


router = APIRouter(prefix="/api")


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return _utc_datetime(value).isoformat().replace("+00:00", "Z")


@router.get(
    "/threatintel/indicator-freshness",
    response_model=IndicatorFreshnessResponse,
)
def indicator_freshness(
    days: int = Query(default=30),
    session=Depends(get_session),
) -> IndicatorFreshnessResponse:
    effective_days = max(1, min(365, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=effective_days)

    type_totals = Counter()
    type_aggregators = Counter()
    type_stale = Counter()
    type_oldest = {}
    type_newest = {}
    source_counts = Counter()
    total_indicators = 0
    total_stale = 0

    rows = session.query(ThreatIntelRef).all()
    for row in rows:
        indicator_type = row.indicator_type
        type_totals[indicator_type] += 1
        total_indicators += 1

        if row.is_aggregator is True:
            type_aggregators[indicator_type] += 1

        if row.source is not None:
            source_counts[row.source] += 1

        if row.fetched_at is None:
            continue

        fetched_at = _utc_datetime(row.fetched_at)
        if fetched_at < cutoff:
            type_stale[indicator_type] += 1
            total_stale += 1

        if indicator_type not in type_oldest or fetched_at < type_oldest[indicator_type]:
            type_oldest[indicator_type] = fetched_at
        if indicator_type not in type_newest or fetched_at > type_newest[indicator_type]:
            type_newest[indicator_type] = fetched_at

    type_rows = [
        IndicatorTypeFreshness(
            indicator_type=indicator_type,
            total=total,
            aggregators=type_aggregators[indicator_type],
            stale_count=type_stale[indicator_type],
            oldest_fetched_at=(
                _utc_iso(type_oldest[indicator_type])
                if indicator_type in type_oldest
                else None
            ),
            newest_fetched_at=(
                _utc_iso(type_newest[indicator_type])
                if indicator_type in type_newest
                else None
            ),
        )
        for indicator_type, total in type_totals.items()
    ]
    type_rows.sort(key=lambda item: (-item.total, item.indicator_type))

    source_rows = [
        SourceCount(source=source, count=count)
        for source, count in sorted(source_counts.items())
    ]

    return IndicatorFreshnessResponse(
        days=effective_days,
        types=type_rows,
        sources=source_rows,
        totals=FreshnessTotals(
            indicators=total_indicators,
            stale=total_stale,
        ),
    )


app = FastAPI()
app.include_router(router)


def main() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=90)
    rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ipv4",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Fresh Pulse",
            pulse_created=now - timedelta(days=1),
            is_aggregator=True,
            source="otx",
            source_url="https://example.test/pulse-1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ipv4",
            indicator_value="192.0.2.2",
            pulse_id="pulse-2",
            pulse_name="Old Pulse",
            pulse_created=old - timedelta(days=1),
            is_aggregator=False,
            source="otx",
            source_url="https://example.test/pulse-2",
            fetched_at=old,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-3",
            pulse_name="Domain Pulse",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="misp",
            source_url="https://example.test/pulse-3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="url",
            indicator_value="https://example.test/indicator",
            pulse_id="pulse-4",
            pulse_name="URL Pulse",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="misp",
            source_url="https://example.test/pulse-4",
            fetched_at=now,
        ),
    ]

    with test_session_factory() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/threatintel/indicator-freshness")
            assert response.status_code == 200
            payload = response.json()
            ipv4 = next(
                item for item in payload["types"] if item["indicator_type"] == "ipv4"
            )
            assert ipv4["total"] == 2
            assert ipv4["aggregators"] == 1
            assert ipv4["stale_count"] == 1
            assert payload["totals"]["indicators"] == 4
            assert payload["totals"]["stale"] == 1
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()