from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class SourceMixGroup(BaseModel):
    source: Optional[str]
    indicator_type: Optional[str]
    is_aggregator: Optional[bool]
    rows: int
    distinct_pulses: int
    distinct_indicators: int


class SourceMixResponse(BaseModel):
    days: int
    generated_at: datetime
    groups: List[SourceMixGroup]


router = APIRouter(prefix="/api")


@router.get("/threat-intel/source-mix", response_model=SourceMixResponse)
def source_mix(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> SourceMixResponse:
    return get_source_mix(days=days, session=session)


def get_source_mix(
    days: int,
    session: Session,
) -> SourceMixResponse:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    statement = (
        select(
            ThreatIntelRef.source.label("source"),
            ThreatIntelRef.indicator_type.label("indicator_type"),
            ThreatIntelRef.is_aggregator.label("is_aggregator"),
            func.count(ThreatIntelRef.id).label("rows"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label(
                "distinct_pulses"
            ),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label(
                "distinct_indicators"
            ),
        )
        .where(ThreatIntelRef.fetched_at >= cutoff)
        .group_by(
            ThreatIntelRef.source,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.is_aggregator,
        )
        .order_by(
            ThreatIntelRef.source,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.is_aggregator,
        )
    )

    rows = session.execute(statement).all()
    groups = [
        SourceMixGroup(
            source=row.source,
            indicator_type=row.indicator_type,
            is_aggregator=row.is_aggregator,
            rows=int(row.rows or 0),
            distinct_pulses=int(row.distinct_pulses or 0),
            distinct_indicators=int(row.distinct_indicators or 0),
        )
        for row in rows
    ]

    return SourceMixResponse(
        days=days,
        generated_at=datetime.now(timezone.utc),
        groups=groups,
    )


compute_source_mix = get_source_mix


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
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
    test_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse 1",
                pulse_created=now,
                is_aggregator=True,
                source="alpha",
                source_url="https://alpha.example/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-1",
                pulse_name="Pulse 1",
                pulse_created=now,
                is_aggregator=True,
                source="alpha",
                source_url="https://alpha.example/2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-2",
                pulse_name="Pulse 2",
                pulse_created=now,
                is_aggregator=True,
                source="alpha",
                source_url="https://alpha.example/3",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.1",
                pulse_id="pulse-3",
                pulse_name="Pulse 3",
                pulse_created=now,
                is_aggregator=False,
                source="beta",
                source_url="https://beta.example/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="sample.test",
                pulse_id="pulse-3",
                pulse_name="Pulse 3",
                pulse_created=now,
                is_aggregator=False,
                source="beta",
                source_url="https://beta.example/2",
                fetched_at=now,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(test_app).get(
        "/api/threat-intel/source-mix", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert sum(group["rows"] for group in payload["groups"]) == 5
    assert any(group["is_aggregator"] is True for group in payload["groups"])
    assert all(
        group["distinct_pulses"] <= group["distinct_indicators"]
        for group in payload["groups"]
    )
    print("PASS")