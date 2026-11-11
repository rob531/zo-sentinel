from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class ThreatIntelPulse(BaseModel):
    pulse_id: str
    pulse_name: Optional[str]
    indicator_type: str
    indicator_value: str
    is_aggregator: bool
    source: str
    source_url: str
    pulse_created: Optional[datetime]
    fetched_at: datetime


class ThreatIntelPulseFilters(BaseModel):
    indicator_type: Optional[str]
    source: Optional[str]
    days: int


class ThreatIntelPulseSearchResponse(BaseModel):
    filters: ThreatIntelPulseFilters
    count: int
    pulses: List[ThreatIntelPulse]


router = APIRouter(prefix="/api")


@router.get(
    "/threatintel/pulses",
    response_model=ThreatIntelPulseSearchResponse,
)
def search_threat_intel_pulses(
    indicator_type: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1),
    limit: int = Query(default=50, ge=1),
    session: Session = Depends(get_session),
) -> ThreatIntelPulseSearchResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(ThreatIntelRef).where(
        ThreatIntelRef.pulse_created >= cutoff
    )

    if indicator_type is not None:
        statement = statement.where(
            ThreatIntelRef.indicator_type == indicator_type
        )
    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)

    statement = statement.order_by(
        ThreatIntelRef.pulse_created.desc()
    ).limit(min(limit, 500))

    rows = session.execute(statement).scalars().all()
    pulses = [
        ThreatIntelPulse(
            pulse_id=str(row.pulse_id),
            pulse_name=row.pulse_name,
            indicator_type=row.indicator_type,
            indicator_value=row.indicator_value,
            is_aggregator=bool(row.is_aggregator),
            source=row.source,
            source_url=row.source_url,
            pulse_created=row.pulse_created,
            fetched_at=row.fetched_at,
        )
        for row in rows
    ]

    return ThreatIntelPulseSearchResponse(
        filters=ThreatIntelPulseFilters(
            indicator_type=indicator_type,
            source=source,
            days=days,
        ),
        count=len(pulses),
        pulses=pulses,
    )


app = FastAPI()
app.include_router(router)


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)

    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    recent = now - timedelta(hours=1)
    old = now - timedelta(days=10)

    seed_rows = [
        ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="203.0.113.10",
            pulse_id="pulse-1001",
            pulse_name="Recent OTX Pulse",
            pulse_created=recent,
            is_aggregator=True,
            source="otx",
            source_url="https://otx.example/pulse-1001",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="old-misp.example",
            pulse_id="pulse-1001",
            pulse_name="Recent OTX Pulse",
            pulse_created=old,
            is_aggregator=False,
            source="misp",
            source_url="https://misp.example/events/pulse-1001",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ipv6",
            indicator_value="2001:db8::10",
            pulse_id="pulse-1002",
            pulse_name="Older OTX Pulse",
            pulse_created=old,
            is_aggregator=False,
            source="otx",
            source_url="https://otx.example/pulse-1002",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="url",
            indicator_value="https://old-misp.example/report",
            pulse_id="pulse-1002",
            pulse_name="Older OTX Pulse",
            pulse_created=old,
            is_aggregator=False,
            source="misp",
            source_url="https://misp.example/events/pulse-1002",
            fetched_at=now,
        ),
    ]

    with session_factory() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/threatintel/pulses",
                params={"days": 30, "limit": 100},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["count"] == 4, payload
            assert len(payload["pulses"]) == 4, payload
            assert sum(
                1 for pulse in payload["pulses"] if pulse["is_aggregator"]
            ) == 1, payload

            filtered = client.get(
                "/api/threatintel/pulses",
                params={"days": 1, "limit": 100},
            )
            assert filtered.status_code == 200, filtered.text
            filtered_payload = filtered.json()
            assert filtered_payload["count"] == 1, filtered_payload
            assert len(filtered_payload["pulses"]) == 1, filtered_payload
            assert filtered_payload["pulses"][0]["indicator_type"] == "ipv4"
            assert filtered_payload["pulses"][0]["source"] == "otx"
            assert filtered_payload["pulses"][0]["is_aggregator"] is True
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()