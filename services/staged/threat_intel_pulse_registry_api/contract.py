import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import case, create_engine, distinct, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class PulseRegistryEntry(BaseModel):
    pulse_id: str
    pulse_name: str
    pulse_created: Optional[datetime]
    indicator_count: int
    aggregator_count: int
    source_count: int


class PulseRegistryResponse(BaseModel):
    pulses: list[PulseRegistryEntry]
    total_pulses: int
    total_indicators: int


router = APIRouter(prefix="/api")


@router.get(
    "/threat/pulses/registry",
    response_model=PulseRegistryResponse,
)
def get_pulse_registry(
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=200),
    session=Depends(get_session),
) -> PulseRegistryResponse:
    limit = max(1, min(limit, 1000))

    predicates = [ThreatIntelRef.pulse_id.isnot(None)]
    if source is not None:
        predicates.append(ThreatIntelRef.source == source)

    indicator_count = func.count(ThreatIntelRef.id)
    aggregator_count = func.sum(
        case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
    )
    source_count = func.count(distinct(ThreatIntelRef.source))

    statement = (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            func.max(ThreatIntelRef.pulse_created).label("pulse_created"),
            indicator_count.label("indicator_count"),
            aggregator_count.label("aggregator_count"),
            source_count.label("source_count"),
        )
        .where(*predicates)
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(indicator_count.desc(), ThreatIntelRef.pulse_id.asc())
        .limit(limit)
    )

    rows = session.execute(statement).all()

    total_pulses = session.execute(
        select(func.count(distinct(ThreatIntelRef.pulse_id))).where(*predicates)
    ).scalar_one()
    total_indicators = session.execute(
        select(func.count(ThreatIntelRef.id)).where(*predicates)
    ).scalar_one()

    return PulseRegistryResponse(
        pulses=[
            PulseRegistryEntry(
                pulse_id=row.pulse_id,
                pulse_name=row.pulse_name or row.pulse_id,
                pulse_created=row.pulse_created,
                indicator_count=int(row.indicator_count or 0),
                aggregator_count=int(row.aggregator_count or 0),
                source_count=int(row.source_count or 0),
            )
            for row in rows
        ],
        total_pulses=int(total_pulses or 0),
        total_indicators=int(total_indicators or 0),
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            is_aggregator=False,
            pulse_created=now,
            pulse_id="pulse-alpha",
            pulse_name="Alpha Threat Pulse",
            source="alpha-source",
            source_url="https://alpha.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="alpha.example",
            is_aggregator=True,
            pulse_created=now,
            pulse_id="pulse-alpha",
            pulse_name="Alpha Threat Pulse",
            source="beta-source",
            source_url="https://beta.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="url",
            indicator_value="https://alpha.example/path",
            is_aggregator=False,
            pulse_created=now,
            pulse_id="pulse-alpha",
            pulse_name="Alpha Threat Pulse",
            source="alpha-source",
            source_url="https://alpha.example/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="198.51.100.1",
            is_aggregator=True,
            pulse_created=now,
            pulse_id="pulse-beta",
            pulse_name="Beta Threat Pulse",
            source="beta-source",
            source_url="https://beta.example/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="beta.example",
            is_aggregator=False,
            pulse_created=now,
            pulse_id="pulse-beta",
            pulse_name="Beta Threat Pulse",
            source="beta-source",
            source_url="https://beta.example/3",
            fetched_at=now,
        ),
    ]

    session = test_session_factory()
    try:
        session.add_all(rows)
        session.commit()

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = lambda: session

        with TestClient(that_app) as client:
            response = client.get("/api/threat/pulses/registry")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_pulses"] == 2, payload
            assert sum(pulse["indicator_count"] for pulse in payload["pulses"]) == 5, payload
            assert payload["total_indicators"] == 5, payload

            alpha = next(
                pulse for pulse in payload["pulses"] if pulse["pulse_id"] == "pulse-alpha"
            )
            assert alpha["source_count"] == 2, payload
            assert alpha["aggregator_count"] == 1, payload
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()