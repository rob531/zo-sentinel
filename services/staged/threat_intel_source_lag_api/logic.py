from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter()


class SourceLagEntry(BaseModel):
    source: str
    indicator_count: int
    pulse_count: int
    newest_fetched_at: Optional[str]
    age_days: Optional[float]
    stale: bool


class IndicatorTypeCount(BaseModel):
    indicator_type: str
    count: int


class ThreatIntelSourceLagResponse(BaseModel):
    days: int
    sources: List[SourceLagEntry]
    by_indicator_type: List[IndicatorTypeCount]
    total_indicators: int
    generated_at: str


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: datetime) -> str:
    return _normalize_datetime(value).isoformat()


@router.get(
    "/threat-intel/source-lag",
    response_model=ThreatIntelSourceLagResponse,
)
def get_source_lag(
    days: int = Query(default=14, ge=0),
    session: Session = Depends(get_session),
) -> ThreatIntelSourceLagResponse:
    now = datetime.now(timezone.utc)

    source_rows = session.execute(
        select(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulse_count"),
            func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
            func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    sources: List[SourceLagEntry] = []
    total_indicators = 0

    for row in source_rows:
        indicator_count = int(row.indicator_count or 0)
        total_indicators += indicator_count
        newest = row.newest_fetched_at
        oldest = row.oldest_fetched_at

        if newest is None:
            newest_fetched_at = None
            age_days = None
            stale = False
        else:
            newest_utc = _normalize_datetime(newest)
            newest_fetched_at = newest_utc.isoformat()
            age_days = (now - newest_utc).total_seconds() / 86400.0
            stale = age_days > days

        if oldest is not None:
            _oldest_fetched_at = _iso8601(oldest)

        sources.append(
            SourceLagEntry(
                source=str(row.source),
                indicator_count=indicator_count,
                pulse_count=int(row.pulse_count or 0),
                newest_fetched_at=newest_fetched_at,
                age_days=age_days,
                stale=stale,
            )
        )

    indicator_rows = session.execute(
        select(
            ThreatIntelRef.indicator_type,
            func.count(ThreatIntelRef.id).label("indicator_count"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    by_indicator_type = [
        IndicatorTypeCount(
            indicator_type=str(row.indicator_type),
            count=int(row.indicator_count or 0),
        )
        for row in indicator_rows
    ]

    return ThreatIntelSourceLagResponse(
        days=days,
        sources=sources,
        by_indicator_type=by_indicator_type,
        total_indicators=total_indicators,
        generated_at=now.isoformat(),
    )


get_threat_intel_source_lag = get_source_lag


if __name__ == "__main__":
    from datetime import timedelta

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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    seed_now = datetime.now(timezone.utc)
    with TestSession() as test_session:
        test_session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    pulse_id="old-pulse-1",
                    pulse_name="Old Pulse 1",
                    source="aged-source",
                    source_url="https://intel.example/old-1",
                    fetched_at=seed_now - timedelta(days=30),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="old.example",
                    pulse_id="old-pulse-2",
                    pulse_name="Old Pulse 2",
                    source="aged-source",
                    source_url="https://intel.example/old-2",
                    fetched_at=seed_now - timedelta(days=20),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.20",
                    pulse_id="fresh-pulse",
                    pulse_name="Fresh Pulse",
                    source="fresh-source",
                    source_url="https://intel.example/fresh-1",
                    fetched_at=seed_now - timedelta(hours=1),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://fresh.example/path",
                    pulse_id="fresh-pulse",
                    pulse_name="Fresh Pulse",
                    source="fresh-source",
                    source_url="https://intel.example/fresh-2",
                    fetched_at=seed_now - timedelta(hours=2),
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router, prefix="/api")

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/source-lag", params={"days": 14})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 14
    assert payload["total_indicators"] == 4
    assert len(payload["sources"]) == 2
    assert sum(item["count"] for item in payload["by_indicator_type"]) == 4

    sources_by_name = {item["source"]: item for item in payload["sources"]}
    assert sources_by_name["aged-source"]["stale"] is True
    assert sources_by_name["fresh-source"]["stale"] is False
    assert sources_by_name["aged-source"]["indicator_count"] == 2
    assert sources_by_name["aged-source"]["pulse_count"] == 2
    assert sources_by_name["fresh-source"]["indicator_count"] == 2
    assert sources_by_name["fresh-source"]["pulse_count"] == 1

    print("PASS")