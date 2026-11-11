from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import ThreatIntelRef


router = APIRouter(prefix="/api", tags=["threat"])


class ThreatPulseResponse(BaseModel):
    days: int
    total_indicators: int
    by_indicator_type: Dict[str, int]
    by_source: Dict[str, int]
    aggregator_ratio: float
    pulses: List["ThreatPulseItem"]


class ThreatPulseItem(BaseModel):
    pulse_id: int
    pulse_name: str
    indicator_count: int
    aggregator_count: int
    indicator_types: int
    sources: int
    pulse_created: datetime
    first_fetched_at: Optional[datetime]
    last_fetched_at: Optional[datetime]


ThreatPulseResponse.model_rebuild()


def get_threat_pulse_growth(
    db: Any,
    days: int = 30,
) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(
            ThreatIntelRef.id,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
            ThreatIntelRef.source_url,
            ThreatIntelRef.fetched_at,
        )
        .filter(ThreatIntelRef.pulse_created >= cutoff)
        .order_by(ThreatIntelRef.pulse_created.asc(), ThreatIntelRef.id.asc())
        .all()
    )

    by_indicator_type: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    grouped: Dict[Any, Dict[str, Any]] = {}
    total_indicators = 0
    total_aggregators = 0

    for row in rows:
        total_indicators += 1

        indicator_type = row.indicator_type
        if indicator_type is not None:
            indicator_type = str(indicator_type)
            by_indicator_type[indicator_type] = (
                by_indicator_type.get(indicator_type, 0) + 1
            )

        source = row.source
        if source is not None:
            source = str(source)
            by_source[source] = by_source.get(source, 0) + 1

        is_aggregator = bool(row.is_aggregator)
        if is_aggregator:
            total_aggregators += 1

        pulse_key = row.pulse_id
        pulse = grouped.get(pulse_key)
        if pulse is None:
            pulse = {
                "pulse_id": pulse_key,
                "pulse_name": row.pulse_name or "",
                "indicator_count": 0,
                "aggregator_count": 0,
                "indicator_types": set(),
                "sources": set(),
                "pulse_created": row.pulse_created,
                "first_fetched_at": None,
                "last_fetched_at": None,
            }
            grouped[pulse_key] = pulse

        pulse["indicator_count"] += 1
        if is_aggregator:
            pulse["aggregator_count"] += 1
        if indicator_type is not None:
            pulse["indicator_types"].add(indicator_type)
        if source is not None:
            pulse["sources"].add(source)

        if row.pulse_created < pulse["pulse_created"]:
            pulse["pulse_created"] = row.pulse_created

        fetched_at = row.fetched_at
        if fetched_at is not None:
            if (
                pulse["first_fetched_at"] is None
                or fetched_at < pulse["first_fetched_at"]
            ):
                pulse["first_fetched_at"] = fetched_at
            if (
                pulse["last_fetched_at"] is None
                or fetched_at > pulse["last_fetched_at"]
            ):
                pulse["last_fetched_at"] = fetched_at

    pulses = [
        {
            "pulse_id": pulse["pulse_id"],
            "pulse_name": pulse["pulse_name"],
            "indicator_count": pulse["indicator_count"],
            "aggregator_count": pulse["aggregator_count"],
            "indicator_types": len(pulse["indicator_types"]),
            "sources": len(pulse["sources"]),
            "pulse_created": pulse["pulse_created"],
            "first_fetched_at": pulse["first_fetched_at"],
            "last_fetched_at": pulse["last_fetched_at"],
        }
        for pulse in grouped.values()
    ]
    pulses.sort(key=lambda pulse: (-pulse["indicator_count"], pulse["pulse_id"]))

    aggregator_ratio = (
        float(total_aggregators) / float(total_indicators)
        if total_indicators
        else 0.0
    )

    return {
        "days": days,
        "total_indicators": total_indicators,
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "aggregator_ratio": aggregator_ratio,
        "pulses": pulses,
    }


@router.get("/threat/pulses", response_model=ThreatPulseResponse)
def threat_pulses(
    days: int = Query(30, ge=1),
    db: Any = Depends(get_session),
) -> Dict[str, Any]:
    return get_threat_pulse_growth(db=db, days=days)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    pulse_id=101,
                    pulse_name="Pulse A",
                    pulse_created=now - timedelta(days=2),
                    is_aggregator=False,
                    source="cert-stream",
                    source_url="https://example.test/a",
                    fetched_at=now - timedelta(days=2),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id=101,
                    pulse_name="Pulse A",
                    pulse_created=now - timedelta(days=2),
                    is_aggregator=False,
                    source="cert-stream",
                    source_url="https://example.test/b",
                    fetched_at=now - timedelta(days=1),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.20",
                    pulse_id=202,
                    pulse_name="Pulse B",
                    pulse_created=now - timedelta(days=4),
                    is_aggregator=True,
                    source="aggregator-feed",
                    source_url="https://example.test/c",
                    fetched_at=now - timedelta(days=3),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat/pulses", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_indicators"] == 3
        assert len(payload["pulses"]) == 2
        assert 0.0 <= payload["aggregator_ratio"] <= 1.0

    print("PASS")