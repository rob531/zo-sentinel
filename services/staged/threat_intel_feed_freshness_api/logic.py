from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import ThreatIntelRef


class SourceFeedFreshness(BaseModel):
    source: str
    count: int
    pulses: int
    is_aggregator: bool
    latest_fetched_at: Optional[str]
    oldest_fetched_at: Optional[str]
    stale_count: int
    median_age_hours: Optional[float]
    max_age_hours: Optional[float]


class FeedFreshnessTotals(BaseModel):
    sources: int
    indicators: int
    stale: int


class ThreatIntelFeedFreshnessResponse(BaseModel):
    max_age_hours: int
    generated_at: str
    sources: List[SourceFeedFreshness]
    indicator_types: Dict[str, int]
    totals: FeedFreshnessTotals


def _clamp_max_age(max_age_hours: int) -> int:
    return max(1, min(720, int(max_age_hours)))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def calculate_feed_freshness(
    session: Any, max_age_hours: int = 48
) -> Dict[str, Any]:
    max_age_hours = _clamp_max_age(max_age_hours)
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=max_age_hours)

    rows = session.query(
        ThreatIntelRef.source,
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.is_aggregator,
        ThreatIntelRef.fetched_at,
        ThreatIntelRef.indicator_type,
    ).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    indicator_types: Dict[str, int] = {}
    total_stale = 0

    for source, pulse_id, is_aggregator, fetched_at, indicator_type in rows:
        source_name = source if source is not None else "unknown"
        indicator_name = (
            indicator_type if indicator_type is not None else "unknown"
        )

        group = grouped.setdefault(
            source_name,
            {
                "count": 0,
                "pulse_ids": set(),
                "is_aggregator": False,
                "fetched_at": [],
                "stale_count": 0,
            },
        )

        group["count"] += 1
        if pulse_id is not None:
            group["pulse_ids"].add(pulse_id)
        if bool(is_aggregator):
            group["is_aggregator"] = True

        indicator_types[indicator_name] = indicator_types.get(indicator_name, 0) + 1

        if fetched_at is not None:
            fetched_at_utc = _as_utc(fetched_at)
            group["fetched_at"].append(fetched_at_utc)
            if fetched_at_utc < cutoff:
                group["stale_count"] += 1
                total_stale += 1

    sources: List[Dict[str, Any]] = []
    for source_name in sorted(grouped):
        group = grouped[source_name]
        fetched_times: List[datetime] = group["fetched_at"]
        ages = [
            (generated_at - fetched_at).total_seconds() / 3600.0
            for fetched_at in fetched_times
        ]

        sources.append(
            {
                "source": source_name,
                "count": group["count"],
                "pulses": len(group["pulse_ids"]),
                "is_aggregator": group["is_aggregator"],
                "latest_fetched_at": _iso8601(
                    max(fetched_times) if fetched_times else None
                ),
                "oldest_fetched_at": _iso8601(
                    min(fetched_times) if fetched_times else None
                ),
                "stale_count": group["stale_count"],
                "median_age_hours": median(ages) if ages else None,
                "max_age_hours": max(ages) if ages else None,
            }
        )

    return {
        "max_age_hours": max_age_hours,
        "generated_at": generated_at.isoformat(),
        "sources": sources,
        "indicator_types": indicator_types,
        "totals": {
            "sources": len(sources),
            "indicators": len(rows),
            "stale": total_stale,
        },
    }


router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threat-intel/feed-freshness",
    response_model=ThreatIntelFeedFreshnessResponse,
)
def get_feed_freshness(
    max_age_hours: int = Query(48),
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return calculate_feed_freshness(session, max_age_hours)


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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    pulse_id="pulse-old",
                    pulse_name="Old feed",
                    pulse_created=now - timedelta(days=2),
                    is_aggregator=False,
                    source="old-source",
                    source_url="https://old-source.invalid",
                    fetched_at=now - timedelta(hours=72),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    pulse_id="pulse-fresh",
                    pulse_name="Fresh feed",
                    pulse_created=now - timedelta(hours=1),
                    is_aggregator=True,
                    source="fresh-source",
                    source_url="https://fresh-source.invalid",
                    fetched_at=now - timedelta(hours=1),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.11",
                    pulse_id="pulse-fresh",
                    pulse_name="Fresh feed",
                    pulse_created=now - timedelta(hours=1),
                    is_aggregator=True,
                    source="fresh-source",
                    source_url="https://fresh-source.invalid",
                    fetched_at=now - timedelta(hours=2),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threat-intel/feed-freshness",
        params={"max_age_hours": 24},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["totals"]["sources"] == 2
    assert payload["totals"]["indicators"] == 3
    assert payload["totals"]["stale"] == 1
    assert payload["indicator_types"]["ip"] == 2
    assert payload["sources"][0]["source"] == "fresh-source"
    assert payload["sources"][0]["stale_count"] == 0
    assert payload["sources"][1]["source"] == "old-source"
    assert payload["sources"][1]["stale_count"] == 1
    print("PASS")