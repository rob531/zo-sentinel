from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api")


class ThreatIntelPulse(BaseModel):
    pulse_id: int
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
    days: Optional[int]


class ThreatIntelPulseSearchResponse(BaseModel):
    filters: ThreatIntelPulseFilters
    count: int
    pulses: List[ThreatIntelPulse]


def search_pulses(
    session: Session,
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 100,
) -> ThreatIntelPulseSearchResponse:
    bounded_limit = min(max(limit, 1), 500)
    statement = select(ThreatIntelRef)

    if indicator_type:
        statement = statement.where(ThreatIntelRef.indicator_type == indicator_type)
    if source:
        statement = statement.where(ThreatIntelRef.source == source)
    if days is not None:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        statement = statement.where(ThreatIntelRef.pulse_created >= cutoff)

    statement = statement.order_by(
        desc(ThreatIntelRef.pulse_created), desc(ThreatIntelRef.id)
    ).limit(bounded_limit)

    rows = session.execute(statement).scalars().all()
    pulses = [
        ThreatIntelPulse(
            pulse_id=row.pulse_id,
            pulse_name=row.pulse_name,
            indicator_type=row.indicator_type,
            indicator_value=row.indicator_value,
            is_aggregator=row.is_aggregator,
            source=row.source,
            source_url=row.source_url,
            pulse_created=row.pulse_created,
            fetched_at=row.fetched_at,
        )
        for row in rows
    ]

    return ThreatIntelPulseSearchResponse(
        filters=ThreatIntelPulseFilters(
            indicator_type=indicator_type or None,
            source=source or None,
            days=days,
        ),
        count=len(pulses),
        pulses=pulses,
    )


@router.get(
    "/threatintel/pulses",
    response_model=ThreatIntelPulseSearchResponse,
)
def threat_intel_pulse_search(
    indicator_type: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=100, ge=1),
    session: Session = Depends(get_session),
) -> ThreatIntelPulseSearchResponse:
    return search_pulses(
        session=session,
        indicator_type=indicator_type,
        source=source,
        days=days,
        limit=limit,
    )


def _self_test() -> None:
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine, checkfirst=True)

    session_factory = Session(bind=engine)

    def override_session():
        with session_factory as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_session
    test_app.include_router(router)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recent = now - timedelta(days=1)
    old = now - timedelta(days=100)

    with session_factory as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.10",
                    pulse_id=1001,
                    pulse_name="Legacy OTX pulse",
                    pulse_created=old,
                    is_aggregator=False,
                    source="otx",
                    source_url="https://otx.example/pulse/1001",
                    fetched_at=old,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.com",
                    pulse_id=1001,
                    pulse_name="Legacy OTX pulse",
                    pulse_created=old,
                    is_aggregator=False,
                    source="otx",
                    source_url="https://otx.example/pulse/1001",
                    fetched_at=old,
                ),
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="198.51.100.20",
                    pulse_id=1002,
                    pulse_name="Recent MISP pulse",
                    pulse_created=recent,
                    is_aggregator=True,
                    source="misp",
                    source_url="https://misp.example/pulse/1002",
                    fetched_at=recent,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="malicious.example",
                    pulse_id=1002,
                    pulse_name="Recent MISP pulse",
                    pulse_created=old,
                    is_aggregator=False,
                    source="misp",
                    source_url="https://misp.example/pulse/1002",
                    fetched_at=old,
                ),
            ]
        )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/threatintel/pulses")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 4
        assert len(payload["pulses"]) == 4
        assert any(pulse["is_aggregator"] is True for pulse in payload["pulses"])

        recent_response = client.get("/api/threatintel/pulses", params={"days": 30})
        assert recent_response.status_code == 200
        recent_payload = recent_response.json()
        assert recent_payload["count"] == 1
        assert recent_payload["pulses"][0]["pulse_id"] == 1002

        filtered_response = client.get(
            "/api/threatintel/pulses",
            params={"indicator_type": "ipv4", "source": "otx"},
        )
        assert filtered_response.status_code == 200
        filtered_payload = filtered_response.json()
        assert filtered_payload["count"] == 1
        assert filtered_payload["pulses"][0]["indicator_value"] == "192.0.2.10"

    print("PASS")


if __name__ == "__main__":
    _self_test()