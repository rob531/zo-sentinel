"""services.staged.threat_intel_aggregator_pulse_api.contract

FastAPI contract for the Threat Intel Aggregator Pulse API.
Provides a GET endpoint that returns aggregated pulse information
derived from the ``threat_intel_refs`` table.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real application data layer imports (must not be stubbed)
from app.db import get_session, Base  # noqa: F401
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api")


class AggregatorPulse(BaseModel):
    pulse_id: str
    pulse_name: str
    is_aggregator: bool
    indicator_count: int
    first_fetched: datetime
    last_fetched: datetime
    sources: List[str]


class AggregatorsResponse(BaseModel):
    aggregators: List[AggregatorPulse]
    total_indicators: int


@router.get(
    "/threat-intel/aggregators",
    response_model=AggregatorsResponse,
    name="get_aggregators",
)
def get_aggregators(
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> AggregatorsResponse:
    """
    Return aggregated information for threat‑intel pulses.

    * ``limit`` caps the number of distinct pulses returned (1‑200, default 50).
    * Aggregation is performed in Python to stay portable between Postgres
      and the in‑memory SQLite used in the self‑test.
    """
    # Distinct pulse identifiers respecting the limit
    pulse_ids_subq = (
        session.query(ThreatIntelRef.pulse_id)
        .distinct()
        .limit(limit)
        .subquery()
    )

    rows = (
        session.query(ThreatIntelRef)
        .filter(ThreatIntelRef.pulse_id.in_(pulse_ids_subq))
        .all()
    )

    total_indicators = len(rows)

    agg_map = {}
    for row in rows:
        pid = row.pulse_id
        if pid not in agg_map:
            agg_map[pid] = {
                "pulse_id": pid,
                "pulse_name": row.pulse_name,
                "is_aggregator": row.is_aggregator,
                "indicator_count": 0,
                "first_fetched": row.fetched_at,
                "last_fetched": row.fetched_at,
                "sources": set(),
            }
        entry = agg_map[pid]
        entry["indicator_count"] += 1
        if row.fetched_at < entry["first_fetched"]:
            entry["first_fetched"] = row.fetched_at
        if row.fetched_at > entry["last_fetched"]:
            entry["last_fetched"] = row.fetched_at
        if row.source:
            entry["sources"].add(row.source)

    aggregators = [
        AggregatorPulse(
            pulse_id=v["pulse_id"],
            pulse_name=v["pulse_name"],
            is_aggregator=v["is_aggregator"],
            indicator_count=v["indicator_count"],
            first_fetched=v["first_fetched"],
            last_fetched=v["last_fetched"],
            sources=sorted(v["sources"]),
        )
        for v in agg_map.values()
    ]

    return AggregatorsResponse(aggregators=aggregators, total_indicators=total_indicators)


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.threat_intel_aggregator_pulse_api.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app with the router and an in‑memory DB
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # SQLite in‑memory engine with StaticPool for thread‑safety in tests
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    # Dependency override to use the test session
    def get_test_session() -> Session:
        return TestSessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed the in‑memory DB with deterministic data
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    test_rows = [
        # Pulse 1 – aggregator, two indicators
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="1.2.3.4",
            pulse_id="pulse-1",
            pulse_name="Aggregator Pulse",
            pulse_created=now,
            is_aggregator=True,
            source="source-A",
            source_url="http://example.com/a",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="bad.example.com",
            pulse_id="pulse-1",
            pulse_name="Aggregator Pulse",
            pulse_created=now,
            is_aggregator=True,
            source="source-B",
            source_url="http://example.com/b",
            fetched_at=now,
        ),
        # Pulse 2 – primary source, two indicators
        ThreatIntelRef(
            id=3,
            indicator_type="ip",
            indicator_value="5.6.7.8",
            pulse_id="pulse-2",
            pulse_name="Primary Pulse",
            pulse_created=now,
            is_aggregator=False,
            source="source-C",
            source_url="http://example.com/c",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="hash",
            indicator_value="deadbeef",
            pulse_id="pulse-2",
            pulse_name="Primary Pulse",
            pulse_created=now,
            is_aggregator=False,
            source="source-D",
            source_url="http://example.com/d",
            fetched_at=now,
        ),
    ]

    with TestSessionLocal() as sess:
        sess.add_all(test_rows)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Execute the request against the test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/threat-intel/aggregators")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Basic shape checks
    assert "aggregators" in data, "Missing 'aggregators' key"
    assert "total_indicators" in data, "Missing 'total_indicators' key"
    assert len(data["aggregators"]) == 2, "Expected 2 pulses"

    # Find the aggregator pulse and validate its fields
    agg_pulse = next(
        (p for p in data["aggregators"] if p["is_aggregator"] is True), None
    )
    assert agg_pulse is not None, "Aggregator pulse not found"
    assert agg_pulse["indicator_count"] == 2, "Aggregator should have 2 indicators"
    assert isinstance(agg_pulse["sources"], list) and len(agg_pulse["sources"]) > 0

    print("PASS")