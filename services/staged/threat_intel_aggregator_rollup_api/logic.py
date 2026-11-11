from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api")


class ThreatIntelSourceRollup(BaseModel):
    source: str
    pulse_name: str
    is_aggregator: bool
    indicators: int
    indicator_types: int
    pulses: int
    last_fetched_at: Optional[str] = None


class ThreatIntelAggregatorRollup(BaseModel):
    total: int
    aggregator_rows: int
    first_party_rows: int
    last_fetched_at: Optional[str] = None
    sources: List[ThreatIntelSourceRollup]


def _utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = _utc_datetime(value)
        if normalized is not None:
            return normalized.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    return str(value)


def _label(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def aggregate_threat_intel_refs(session: Any) -> Dict[str, Any]:
    rows = (
        session.query(
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.fetched_at,
        ).all()
    )

    groups: Dict[Tuple[Any, Any, bool], Dict[str, Any]] = {}
    total = 0
    aggregator_rows = 0
    newest: Optional[datetime] = None

    for source, pulse_name, is_aggregator, indicator_type, pulse_id, fetched_at in rows:
        total += 1
        aggregator = bool(is_aggregator)
        if aggregator:
            aggregator_rows += 1

        timestamp = _utc_datetime(fetched_at)
        if timestamp is not None and (newest is None or timestamp > newest):
            newest = timestamp

        key = (source, pulse_name, aggregator)
        group = groups.setdefault(
            key,
            {
                "indicators": 0,
                "indicator_types": set(),
                "pulses": set(),
                "last_fetched_at": None,
            },
        )
        group["indicators"] += 1
        if indicator_type is not None:
            group["indicator_types"].add(indicator_type)
        if pulse_id is not None:
            group["pulses"].add(pulse_id)
        if timestamp is not None:
            current = group["last_fetched_at"]
            if current is None or timestamp > current:
                group["last_fetched_at"] = timestamp

    sources = [
        ThreatIntelSourceRollup(
            source=_label(source),
            pulse_name=_label(pulse_name),
            is_aggregator=is_aggregator,
            indicators=group["indicators"],
            indicator_types=len(group["indicator_types"]),
            pulses=len(group["pulses"]),
            last_fetched_at=_iso_timestamp(group["last_fetched_at"]),
        )
        for (source, pulse_name, is_aggregator), group in groups.items()
    ]
    sources.sort(
        key=lambda item: (
            -item.indicators,
            item.source,
            item.pulse_name,
            not item.is_aggregator,
        )
    )

    return ThreatIntelAggregatorRollup(
        total=total,
        aggregator_rows=aggregator_rows,
        first_party_rows=total - aggregator_rows,
        last_fetched_at=_iso_timestamp(newest),
        sources=sources,
    )


def get_threat_intel_aggregators(session: Any = Depends(get_session)) -> ThreatIntelAggregatorRollup:
    return aggregate_threat_intel_refs(session)


get_aggregator_rollup = aggregate_threat_intel_refs
list_threat_intel_aggregators = aggregate_threat_intel_refs

router.add_api_route(
    "/threat-intel/aggregators",
    get_threat_intel_aggregators,
    methods=["GET"],
    response_model=ThreatIntelAggregatorRollup,
)


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=engine)

    test_session = TestSession()
    test_session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id=101,
                pulse_name="Example Aggregator Pulse",
                pulse_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
                is_aggregator=True,
                source="example-aggregator",
                source_url="https://aggregator.example/pulse/101",
                fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id=101,
                pulse_name="Example Aggregator Pulse",
                pulse_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
                is_aggregator=True,
                source="example-aggregator",
                source_url="https://aggregator.example/pulse/101",
                fetched_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                indicator_type="hash",
                indicator_value="a" * 64,
                pulse_id=202,
                pulse_name="First Party Pulse",
                pulse_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
                is_aggregator=False,
                source="first-party-vendor",
                source_url="https://vendor.example/pulse/202",
                fetched_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat-intel/aggregators")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["aggregator_rows"] == 2
    assert payload["first_party_rows"] == 1
    assert len(payload["sources"]) == 2
    print("PASS")