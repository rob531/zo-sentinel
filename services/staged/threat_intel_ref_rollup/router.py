"""HTTP surface for the threat-intelligence reference rollup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import rollup as _logic_rollup
except ImportError:
    _logic_rollup = None


router = APIRouter(prefix="/api", tags=["threat-intel"])


class PulseRollup(BaseModel):
    pulse_id: str
    pulse_name: Optional[str] = None
    pulse_created: Optional[datetime] = None
    ref_count: int


class ThreatIntelRollupResponse(BaseModel):
    window_days: int
    total_refs: int
    by_indicator_type: dict[str, int]
    by_source: dict[str, int]
    distinct_pulses: int
    aggregator_refs: int
    direct_refs: int
    recent_pulses: list[PulseRollup]


def _calculate_rollup(db: Session, days: int, limit: int) -> ThreatIntelRollupResponse:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    window = ThreatIntelRef.fetched_at >= cutoff

    total_refs = db.execute(
        select(func.count()).select_from(ThreatIntelRef).where(window)
    ).scalar_one()

    indicator_rows = db.execute(
        select(ThreatIntelRef.indicator_type, func.count())
        .where(window)
        .group_by(ThreatIntelRef.indicator_type)
    ).all()
    source_rows = db.execute(
        select(ThreatIntelRef.source, func.count())
        .where(window)
        .group_by(ThreatIntelRef.source)
    ).all()
    distinct_pulses = db.execute(
        select(func.count(func.distinct(ThreatIntelRef.pulse_id))).where(window)
    ).scalar_one()
    aggregator_refs = db.execute(
        select(func.count())
        .select_from(ThreatIntelRef)
        .where(window, ThreatIntelRef.is_aggregator.is_(True))
    ).scalar_one()
    direct_refs = db.execute(
        select(func.count())
        .select_from(ThreatIntelRef)
        .where(window, ThreatIntelRef.is_aggregator.is_(False))
    ).scalar_one()
    pulse_rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
            func.count().label("ref_count"),
        )
        .where(window)
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
        )
        .order_by(ThreatIntelRef.pulse_created.desc().nullslast())
        .limit(limit)
    ).all()

    return ThreatIntelRollupResponse(
        window_days=days,
        total_refs=int(total_refs or 0),
        by_indicator_type={str(row[0]): int(row[1]) for row in indicator_rows},
        by_source={str(row[0]): int(row[1]) for row in source_rows},
        distinct_pulses=int(distinct_pulses or 0),
        aggregator_refs=int(aggregator_refs or 0),
        direct_refs=int(direct_refs or 0),
        recent_pulses=[
            PulseRollup(
                pulse_id=str(row[0]),
                pulse_name=row[1],
                pulse_created=row[2],
                ref_count=int(row[3]),
            )
            for row in pulse_rows
        ],
    )


@router.get(
    "/threat-intel/rollup",
    response_model=ThreatIntelRollupResponse,
)
def get_threat_intel_rollup(
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> ThreatIntelRollupResponse:
    return _calculate_rollup(db, days, limit)


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
    testing_session = sessionmaker(bind=engine)()

    testing_session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Example Pulse 1",
                pulse_created=datetime(2024, 1, 1),
                is_aggregator=True,
                source="example-a",
                source_url="https://example.test/a",
                fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-1",
                pulse_name="Example Pulse 1",
                pulse_created=datetime(2024, 1, 1),
                is_aggregator=False,
                source="example-a",
                source_url="https://example.test/a",
                fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-2",
                pulse_name="Example Pulse 2",
                pulse_created=datetime(2024, 1, 2),
                is_aggregator=True,
                source="example-b",
                source_url="https://example.test/b",
                fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            ThreatIntelRef(
                indicator_type="url",
                indicator_value="https://example.test/malware",
                pulse_id="pulse-2",
                pulse_name="Example Pulse 2",
                pulse_created=datetime(2024, 1, 2),
                is_aggregator=False,
                source="example-b",
                source_url="https://example.test/b",
                fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/threat-intel/rollup")
    response.raise_for_status()
    payload = response.json()
    assert payload["total_refs"] == 4
    assert len(payload["by_indicator_type"]) >= 1
    assert payload["distinct_pulses"] == 2
    assert payload["aggregator_refs"] + payload["direct_refs"] == 4
    print("PASS")