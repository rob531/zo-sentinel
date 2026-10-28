from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


router = APIRouter(prefix="/api")


class IndicatorTypeCount(BaseModel):
    indicator_type: str
    count: int


class ReferenceSideStats(BaseModel):
    refs: int
    pulses: int
    indicator_types: int
    top_types: List[IndicatorTypeCount]


class AggregatorIsolationResponse(BaseModel):
    days: int
    window_start: datetime
    aggregator: ReferenceSideStats
    primary: ReferenceSideStats
    ratio: float
    total_refs: int


def _side_stats(refs: List[ThreatIntelRef]) -> ReferenceSideStats:
    type_counts = Counter(
        ref.indicator_type for ref in refs if ref.indicator_type is not None
    )
    top_types = [
        IndicatorTypeCount(indicator_type=indicator_type, count=count)
        for indicator_type, count in sorted(
            type_counts.items(), key=lambda item: (-item[1], item[0])
        )[:20]
    ]
    return ReferenceSideStats(
        refs=len(refs),
        pulses=len(
            {
                ref.pulse_id
                for ref in refs
                if ref.pulse_id is not None
            }
        ),
        indicator_types=len(type_counts),
        top_types=top_types,
    )


@router.get(
    "/threat-intel/aggregator-isolation",
    response_model=AggregatorIsolationResponse,
)
def get_aggregator_isolation(
    days: int = Query(default=30, ge=0, le=3650),
    session: Session = Depends(get_session),
) -> AggregatorIsolationResponse:
    window_start = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(ThreatIntelRef).where(
        ThreatIntelRef.fetched_at >= window_start
    )
    refs = list(session.execute(statement).scalars().all())

    aggregator_refs = [ref for ref in refs if ref.is_aggregator is True]
    primary_refs = [ref for ref in refs if ref.is_aggregator is False]
    total_refs = len(refs)

    return AggregatorIsolationResponse(
        days=days,
        window_start=window_start,
        aggregator=_side_stats(aggregator_refs),
        primary=_side_stats(primary_refs),
        ratio=(len(aggregator_refs) / total_refs) if total_refs else 0.0,
        total_refs=total_refs,
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    ThreatIntelRef.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="198.51.100.1",
            pulse_id="pulse-a",
            pulse_name="Alpha",
            pulse_created=now,
            is_aggregator=True,
            source="aggregator-alpha",
            source_url="https://alpha.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="malicious.example",
            pulse_id="pulse-a",
            pulse_name="Alpha",
            pulse_created=now,
            is_aggregator=True,
            source="aggregator-alpha",
            source_url="https://alpha.example/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="203.0.113.2",
            pulse_id="pulse-b",
            pulse_name="Beta",
            pulse_created=now,
            is_aggregator=True,
            source="aggregator-beta",
            source_url="https://beta.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-c",
            pulse_name="Gamma",
            pulse_created=now,
            is_aggregator=False,
            source="primary-a",
            source_url="https://primary-a.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="direct.example",
            pulse_id="pulse-c",
            pulse_name="Gamma",
            pulse_created=now,
            is_aggregator=False,
            source="primary-a",
            source_url="https://primary-a.example/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-d",
            pulse_name="Delta",
            pulse_created=now,
            is_aggregator=False,
            source="primary-b",
            source_url="https://primary-b.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="another-direct.example",
            pulse_id="pulse-d",
            pulse_name="Delta",
            pulse_created=now,
            is_aggregator=False,
            source="primary-b",
            source_url="https://primary-b.example/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="pulse-e",
            pulse_name="Epsilon",
            pulse_created=now,
            is_aggregator=False,
            source="primary-c",
            source_url="https://primary-c.example/1",
            fetched_at=now,
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

        that_app = FastAPI()
        that_app.include_router(router)

        def override_get_session():
            with testing_session() as override_session:
                yield override_session

        that_app.dependency_overrides[get_session] = override_get_session

        from fastapi.testclient import TestClient

        with TestClient(that_app) as client:
            response = client.get(
                "/api/threat-intel/aggregator-isolation",
                params={"days": 30},
            )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["aggregator"]["refs"] == 3, payload
    assert payload["primary"]["refs"] == 5, payload
    assert payload["total_refs"] == 8, payload
    assert payload["ratio"] == 0.375, payload
    print("PASS")


if __name__ == "__main__":
    _run_self_test()