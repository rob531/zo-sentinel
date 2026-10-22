"""
services/staged/threat_intel_pulse_ranking_api/contract.py
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, distinct, case
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api")


# ---------- Pydantic response models ----------
class PulseInfo(BaseModel):
    pulse_id: int = Field(..., description="Pulse identifier")
    pulse_name: str = Field(..., description="Pulse name")
    indicator_count: int = Field(..., description="Total number of indicators")
    distinct_indicator_types: int = Field(..., description="Number of distinct indicator types")
    first_seen: datetime.datetime = Field(..., description="Earliest pulse_created timestamp")
    last_fetched: datetime.datetime = Field(..., description="Most recent fetched_at timestamp")
    is_aggregator: bool = Field(..., description="Whether any indicator is from an aggregator source")


class PulseRankingResponse(BaseModel):
    generated_at: datetime.datetime = Field(..., description="Timestamp of response generation")
    limit: int = Field(..., description="Limit applied to the query")
    pulses: List[PulseInfo] = Field(..., description="List of ranked pulses")


# ---------- Endpoint implementation ----------
@router.get(
    "/threat-intel/pulse-ranking",
    response_model=PulseRankingResponse,
    summary="Rank threat‑intel pulses by indicator count",
)
def get_pulse_ranking(
    limit: int = Query(10, ge=1, description="Maximum number of pulses to return"),
    session: Session = Depends(get_session),
) -> PulseRankingResponse:
    """
    Rank pulses by the total number of indicators they contain.
    """
    # Build the aggregation query
    stmt = (
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            func.count().label("indicator_count"),
            func.count(distinct(ThreatIntelRef.indicator_type)).label("distinct_indicator_types"),
            func.min(ThreatIntelRef.pulse_created).label("first_seen"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched"),
            func.max(
                case((ThreatIntelRef.is_aggregator.is_(True), True), else_=False)
            ).label("is_aggregator"),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(func.count().desc())
        .limit(limit)
    )

    rows = session.execute(stmt).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No pulse data found")

    pulses = [
        PulseInfo(
            pulse_id=row.pulse_id,
            pulse_name=row.pulse_name,
            indicator_count=row.indicator_count,
            distinct_indicator_types=row.distinct_indicator_types,
            first_seen=row.first_seen,
            last_fetched=row.last_fetched,
            is_aggregator=row.is_aggregator,
        )
        for row in rows
    ]

    return PulseRankingResponse(
        generated_at=datetime.datetime.utcnow(),
        limit=limit,
        pulses=pulses,
    )


# ---------- Self‑test (run with `python -m services.staged.threat_intel_pulse_ranking_api.contract`) ----------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in‑memory SQLite engine compatible with the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables for the models we use
    ThreatIntelRef.__table__.create(bind=engine)

    # Seed test data
    test_session = SessionLocal()
    now = datetime.datetime.utcnow()
    pulse1_id = 1
    pulse2_id = 2

    test_rows = [
        # Pulse 1 – three indicators, two distinct types
        ThreatIntelRef(
            id=1,
            indicator_type="type_a",
            indicator_value="value1",
            pulse_id=pulse1_id,
            pulse_name="Pulse One",
            pulse_created=now - datetime.timedelta(days=2),
            is_aggregator=False,
            source="src",
            source_url="http://example.com/1",
            fetched_at=now - datetime.timedelta(days=1),
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="type_b",
            indicator_value="value2",
            pulse_id=pulse1_id,
            pulse_name="Pulse One",
            pulse_created=now - datetime.timedelta(days=2),
            is_aggregator=False,
            source="src",
            source_url="http://example.com/2",
            fetched_at=now - datetime.timedelta(hours=12),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="type_a",
            indicator_value="value3",
            pulse_id=pulse1_id,
            pulse_name="Pulse One",
            pulse_created=now - datetime.timedelta(days=2),
            is_aggregator=False,
            source="src",
            source_url="http://example.com/3",
            fetched_at=now - datetime.timedelta(hours=6),
        ),
        # Pulse 2 – single indicator
        ThreatIntelRef(
            id=4,
            indicator_type="type_c",
            indicator_value="value4",
            pulse_id=pulse2_id,
            pulse_name="Pulse Two",
            pulse_created=now - datetime.timedelta(days=1),
            is_aggregator=False,
            source="src",
            source_url="http://example.com/4",
            fetched_at=now - datetime.timedelta(hours=3),
        ),
    ]

    test_session.add_all(test_rows)
    test_session.commit()

    # Build FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:
        return test_session

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/threat-intel/pulse-ranking?limit=10")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    pulses = data.get("pulses", [])
    if len(pulses) != 2:
        print(f"FAIL: Expected 2 pulses, got {len(pulses)}", file=sys.stderr)
        sys.exit(1)

    top = pulses[0]
    if top["indicator_count"] != 3:
        print(
            f"FAIL: Expected top pulse indicator_count 3, got {top['indicator_count']}",
            file=sys.stderr,
        )
        sys.exit(1)

    if top["distinct_indicator_types"] != 2:
        print(
            f"FAIL: Expected top pulse distinct_indicator_types 2, got {top['distinct_indicator_types']}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("PASS")
    sys.exit(0)