from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class PulseRegistryItem(BaseModel):
    pulse_id: str
    pulse_name: str
    pulse_created: Optional[datetime]
    indicator_count: int
    aggregator_count: int
    source_count: int


class PulseRegistryResponse(BaseModel):
    pulses: list[PulseRegistryItem]
    total_pulses: int
    total_indicators: int


router = APIRouter(tags=["threat-intel"])


@router.get(
    "/threat/pulses/registry",
    response_model=PulseRegistryResponse,
    summary="Get the threat intelligence pulse registry",
)
def get_pulse_registry(
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=200),
    session: Session = Depends(get_session),
) -> PulseRegistryResponse:
    limit = max(1, min(limit, 1000))

    statement = (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            func.max(ThreatIntelRef.pulse_name).label("pulse_name"),
            func.max(ThreatIntelRef.pulse_created).label("pulse_created"),
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.sum(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ).label("aggregator_count"),
            func.count(distinct(ThreatIntelRef.source)).label("source_count"),
        )
        .group_by(ThreatIntelRef.pulse_id)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
    )

    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)

    rows = session.execute(statement).all()
    pulses = [
        PulseRegistryItem(
            pulse_id=row.pulse_id,
            pulse_name=row.pulse_name,
            pulse_created=row.pulse_created,
            indicator_count=int(row.indicator_count or 0),
            aggregator_count=int(row.aggregator_count or 0),
            source_count=int(row.source_count or 0),
        )
        for row in rows[:limit]
    ]

    return PulseRegistryResponse(
        pulses=pulses,
        total_pulses=len(rows),
        total_indicators=sum(int(row.indicator_count or 0) for row in rows),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as test_session:
        test_session.add_all(
            [
                ThreatIntelRef(
                    pulse_id="pulse-alpha",
                    pulse_name="Alpha Pulse",
                    pulse_created=now,
                    indicator_type="domain",
                    indicator_value="alpha.example",
                    is_aggregator=False,
                    source="source-a",
                    source_url="https://source-a.example/alpha",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    pulse_id="pulse-alpha",
                    pulse_name="Alpha Pulse",
                    pulse_created=now,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.10",
                    is_aggregator=True,
                    source="source-b",
                    source_url="https://source-b.example/alpha",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    pulse_id="pulse-alpha",
                    pulse_name="Alpha Pulse",
                    pulse_created=now,
                    indicator_type="url",
                    indicator_value="https://alpha.example/path",
                    is_aggregator=False,
                    source="source-a",
                    source_url="https://source-a.example/alpha/url",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    pulse_id="pulse-beta",
                    pulse_name="Beta Pulse",
                    pulse_created=now,
                    indicator_type="domain",
                    indicator_value="beta.example",
                    is_aggregator=False,
                    source="source-a",
                    source_url="https://source-a.example/beta",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    pulse_id="pulse-beta",
                    pulse_name="Beta Pulse",
                    pulse_created=now,
                    indicator_type="hash",
                    indicator_value="abcdef1234567890",
                    is_aggregator=True,
                    source="source-b",
                    source_url="https://source-b.example/beta",
                    fetched_at=now,
                ),
            ]
        )
        test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat/pulses/registry")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_pulses"] == 2, payload
        assert payload["total_indicators"] == 5, payload
        assert sum(item["indicator_count"] for item in payload["pulses"]) == 5, payload
        assert all(item["source_count"] == 2 for item in payload["pulses"]), payload

    print("PASS")