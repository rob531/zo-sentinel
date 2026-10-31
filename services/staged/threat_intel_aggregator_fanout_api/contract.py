from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class SourceCount(BaseModel):
    source: str
    count: int


class PulseFanout(BaseModel):
    pulse_id: str
    pulse_name: str
    indicator_count: int
    last_fetched: str


class ThreatIntelFanoutResponse(BaseModel):
    total: int
    aggregator_count: int
    direct_count: int
    by_indicator_type: Dict[str, int]
    by_source: List[SourceCount]
    pulses: List[PulseFanout]
    stalest_fetch_hours: Optional[float]


router = APIRouter(prefix="/api")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso8601(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get(
    "/threat_intel/fanout",
    response_model=ThreatIntelFanoutResponse,
)
def get_threat_intel_fanout(
    days: int = Query(30),
    session=Depends(get_session),
) -> ThreatIntelFanoutResponse:
    cutoff = _utcnow() - timedelta(days=days)
    rows = list(
        session.execute(
            select(ThreatIntelRef).where(ThreatIntelRef.fetched_at >= cutoff)
        ).scalars()
    )

    aggregator_count = sum(1 for row in rows if bool(row.is_aggregator))
    direct_count = len(rows) - aggregator_count

    indicator_counts = Counter(
        row.indicator_type for row in rows if row.indicator_type is not None
    )
    source_counts = Counter(row.source for row in rows if row.source is not None)

    pulse_map: Dict[str, Dict[str, object]] = {}
    fetch_times = []
    for row in rows:
        pulse_id = str(row.pulse_id)
        pulse = pulse_map.setdefault(
            pulse_id,
            {
                "pulse_id": pulse_id,
                "pulse_name": str(row.pulse_name or ""),
                "indicator_count": 0,
                "last_fetched": None,
            },
        )
        pulse["indicator_count"] = int(pulse["indicator_count"]) + 1
        if row.fetched_at is not None:
            fetch_times.append(row.fetched_at)
            current = pulse["last_fetched"]
            if current is None or row.fetched_at > current:
                pulse["last_fetched"] = row.fetched_at

    pulses = []
    for pulse in pulse_map.values():
        last_fetched = pulse["last_fetched"]
        pulses.append(
            PulseFanout(
                pulse_id=str(pulse["pulse_id"]),
                pulse_name=str(pulse["pulse_name"]),
                indicator_count=int(pulse["indicator_count"]),
                last_fetched=_iso8601(last_fetched),
            )
        )
    pulses.sort(key=lambda pulse: (pulse.pulse_id, pulse.pulse_name))

    stalest_fetch_hours = None
    if fetch_times:
        oldest = min(fetch_times)
        now = _utcnow()
        if oldest.tzinfo is not None:
            oldest = oldest.astimezone(timezone.utc).replace(tzinfo=None)
        stalest_fetch_hours = max(
            0.0, (now - oldest).total_seconds() / 3600.0
        )

    return ThreatIntelFanoutResponse(
        total=len(rows),
        aggregator_count=aggregator_count,
        direct_count=direct_count,
        by_indicator_type=dict(sorted(indicator_counts.items())),
        by_source=[
            SourceCount(source=source, count=count)
            for source, count in sorted(
                source_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        pulses=pulses,
        stalest_fetch_hours=stalest_fetch_hours,
    )


app = FastAPI()
app.include_router(router)


def run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = _utcnow()
    seed_rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now - timedelta(days=2),
            is_aggregator=True,
            source="aggregator-one",
            source_url="https://example.test/a",
            fetched_at=now - timedelta(hours=2),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now - timedelta(days=2),
            is_aggregator=True,
            source="aggregator-one",
            source_url="https://example.test/b",
            fetched_at=now - timedelta(hours=1),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="198.51.100.2",
            pulse_id="pulse-b",
            pulse_name="Pulse B",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="direct-feed",
            source_url="https://example.test/c",
            fetched_at=now - timedelta(hours=3),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="direct.example",
            pulse_id="pulse-b",
            pulse_name="Pulse B",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="direct-feed",
            source_url="https://example.test/d",
            fetched_at=now - timedelta(minutes=30),
        ),
    ]

    with TestSession() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat_intel/fanout")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["aggregator_count"] == 2
        assert payload["direct_count"] == 2
        assert len(payload["pulses"]) == 2
        assert payload["stalest_fetch_hours"] is not None

    test_engine.dispose()


if __name__ == "__main__":
    run_self_test()
    print("PASS")