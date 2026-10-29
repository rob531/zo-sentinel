from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class SourceFetchLag(BaseModel):
    source: str
    total: int
    stale_count: int
    max_age_hours: float
    oldest_fetched_at: datetime


class IndicatorTypeFetchLag(BaseModel):
    indicator_type: str
    count: int
    max_age_hours: float


class FetchLagResponse(BaseModel):
    hours: int
    generated_at: datetime
    by_source: List[SourceFetchLag]
    by_indicator_type: List[IndicatorTypeFetchLag]
    overall_max_age_hours: float
    total: int


router = APIRouter(prefix="/api")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_hours(value: Optional[datetime], now: datetime) -> float:
    if value is None:
        return 0.0
    return max(0.0, (now - _as_utc(value)).total_seconds() / 3600.0)


def _iso_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return _as_utc(value)


@router.get(
    "/threat-intel/fetch-lag",
    response_model=FetchLagResponse,
    summary="Report threat-intel reference cache fetch lag",
)
def fetch_lag(
    hours: int = Query(default=168, ge=0),
    source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> FetchLagResponse:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    filters = []
    if source is not None:
        filters.append(ThreatIntelRef.source == source)

    stale_expression = case(
        (ThreatIntelRef.fetched_at < cutoff, 1),
        else_=0,
    )

    source_stmt = select(
        ThreatIntelRef.source,
        func.count(ThreatIntelRef.id).label("total"),
        func.coalesce(func.sum(stale_expression), 0).label("stale_count"),
        func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
    )
    indicator_stmt = select(
        ThreatIntelRef.indicator_type,
        func.count(ThreatIntelRef.id).label("count"),
        func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
    )
    overall_stmt = select(
        func.count(ThreatIntelRef.id).label("total"),
        func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
    )

    if filters:
        source_stmt = source_stmt.where(*filters)
        indicator_stmt = indicator_stmt.where(*filters)
        overall_stmt = overall_stmt.where(*filters)

    source_stmt = source_stmt.group_by(ThreatIntelRef.source)
    indicator_stmt = indicator_stmt.group_by(ThreatIntelRef.indicator_type)

    source_rows = session.execute(source_stmt).all()
    indicator_rows = session.execute(indicator_stmt).all()
    overall_row = session.execute(overall_stmt).one()

    by_source = []
    for row in source_rows:
        oldest = _iso_datetime(row.oldest_fetched_at)
        by_source.append(
            {
                "source": row.source or "",
                "total": int(row.total),
                "stale_count": int(row.stale_count or 0),
                "max_age_hours": round(_age_hours(oldest, now), 1),
                "oldest_fetched_at": oldest,
            }
        )

    by_source.sort(key=lambda item: (-item["total"], item["source"]))

    by_indicator_type = []
    for row in indicator_rows:
        oldest = _iso_datetime(row.oldest_fetched_at)
        by_indicator_type.append(
            {
                "indicator_type": row.indicator_type or "",
                "count": int(row.count),
                "max_age_hours": round(_age_hours(oldest, now), 1),
            }
        )

    by_indicator_type.sort(
        key=lambda item: (-item["count"], item["indicator_type"])
    )

    overall_oldest = _iso_datetime(overall_row.oldest_fetched_at)

    return FetchLagResponse(
        hours=hours,
        generated_at=now,
        by_source=by_source,
        by_indicator_type=by_indicator_type,
        overall_max_age_hours=round(_age_hours(overall_oldest, now), 1),
        total=int(overall_row.total or 0),
    )


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value=f"192.0.2.{index}",
            pulse_id=f"pulse-abuse-{index}",
            pulse_name="Abuse pulse",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="abuse.ch",
            source_url=f"https://abuse.ch/item/{index}",
            fetched_at=now - timedelta(hours=2),
        )
        for index in range(1, 4)
    ]
    rows.extend(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value=f"198.51.100.{index}",
                pulse_id=f"pulse-circl-{index}",
                pulse_name="CIRCL pulse",
                pulse_created=now - timedelta(days=20),
                is_aggregator=False,
                source="circl.lu",
                source_url=f"https://circl.lu/item/{index}",
                fetched_at=now - timedelta(hours=400),
            )
            for index in range(1, 3)
        ]
    )
    rows.append(
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.com",
            pulse_id="pulse-domain",
            pulse_name="Domain pulse",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="example.com",
            source_url="https://example.com/item",
            fetched_at=now - timedelta(hours=10),
        )
    )

    session = TestingSession()
    try:
        session.add_all(rows)
        session.commit()
    finally:
        session.close()

    client = TestClient(test_app)
    response = client.get("/api/threat-intel/fetch-lag")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 6, payload

    circl = next(
        item for item in payload["by_source"] if item["source"] == "circl.lu"
    )
    assert circl["stale_count"] == 2, payload
    assert circl["max_age_hours"] >= 400, payload

    domain = next(
        item
        for item in payload["by_indicator_type"]
        if item["indicator_type"] == "domain"
    )
    assert domain["count"] == 1, payload

    print("PASS")


if __name__ == "__main__":
    _self_test()