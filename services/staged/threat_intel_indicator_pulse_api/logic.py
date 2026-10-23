# services/staged/threat_intel_indicator_pulse_api/logic.py
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import ThreatIntelRef

router = APIRouter()


@router.get(
    "/threatintel/pulses",
    response_model=dict,
    summary="Breakdown of ingested threat‑intel indicators",
)
def get_threat_intel_pulses(
    *,
    session: Session = Depends(get_session),
    indicator_type: Optional[str] = Query(
        None, description="Filter by indicator type"
    ),
    is_aggregator: Optional[bool] = Query(
        None, description="Filter by aggregator flag"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Maximum rows to return"),
):
    """
    Returns a JSON payload containing:
    * total number of matching rows
    * aggregation by indicator_type
    * aggregation by pulse
    * the most recent rows (ordered by fetched_at desc)
    """
    # ------------------------------------------------------------------ #
    # Base query with optional filters
    # ------------------------------------------------------------------ #
    base_q = session.query(ThreatIntelRef)
    if indicator_type is not None:
        base_q = base_q.filter(ThreatIntelRef.indicator_type == indicator_type)
    if is_aggregator is not None:
        base_q = base_q.filter(ThreatIntelRef.is_aggregator == is_aggregator)

    # ------------------------------------------------------------------ #
    # Total count
    # ------------------------------------------------------------------ #
    total = base_q.count()

    # ------------------------------------------------------------------ #
    # By indicator type aggregation
    # ------------------------------------------------------------------ #
    by_indicator_type_q = (
        session.query(
            ThreatIntelRef.indicator_type,
            func.count().label("count"),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label(
                "distinct_values"
            ),
        )
        .filter(
            *(  # reuse same filters
                (ThreatIntelRef.indicator_type == indicator_type,)
                if indicator_type is not None
                else ()
            ),
            *(  # aggregator filter
                (ThreatIntelRef.is_aggregator == is_aggregator,)
                if is_aggregator is not None
                else ()
            ),
        )
        .group_by(ThreatIntelRef.indicator_type)
    )
    by_indicator_type = [
        {
            "indicator_type": row.indicator_type,
            "count": row.count,
            "distinct_values": row.distinct_values,
        }
        for row in by_indicator_type_q
    ]

    # ------------------------------------------------------------------ #
    # By pulse aggregation
    # ------------------------------------------------------------------ #
    by_pulse_q = (
        session.query(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
            func.count().label("count"),
            ThreatIntelRef.is_aggregator.label("aggregator"),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
        )
        .filter(
            *(  # same filters as base
                (ThreatIntelRef.indicator_type == indicator_type,)
                if indicator_type is not None
                else ()
            ),
            *(  # aggregator filter
                (ThreatIntelRef.is_aggregator == is_aggregator,)
                if is_aggregator is not None
                else ()
            ),
        )
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.is_aggregator,
        )
    )
    by_pulse = [
        {
            "pulse_id": row.pulse_id,
            "pulse_name": row.pulse_name,
            "pulse_created": row.pulse_created.isoformat(),
            "count": row.count,
            "aggregator": row.aggregator,
            "latest_fetched_at": row.latest_fetched_at.isoformat(),
        }
        for row in by_pulse_q
    ]

    # ------------------------------------------------------------------ #
    # Row list (most recent first)
    # ------------------------------------------------------------------ #
    rows_q = (
        base_q.order_by(ThreatIntelRef.fetched_at.desc()).limit(limit)
    )
    rows = [
        {
            "id": r.id,
            "indicator_type": r.indicator_type,
            "indicator_value": r.indicator_value,
            "pulse_name": r.pulse_name,
            "source": r.source,
            "source_url": r.source_url,
            "fetched_at": r.fetched_at.isoformat(),
        }
        for r in rows_q
    ]

    return {
        "total": total,
        "by_indicator_type": by_indicator_type,
        "by_pulse": by_pulse,
        "rows": rows,
    }


# ---------------------------------------------------------------------- #
# Self‑test when executed as a script
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------ #
    # In‑memory SQLite setup
    # ------------------------------------------------------------------ #
    TEST_DATABASE_URL = "sqlite://"
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------ #
    # Populate with deterministic data
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sample_data = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="1.2.3.4",
            pulse_id=10,
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=False,
            source="source-a",
            source_url="http://a.example",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="5.6.7.8",
            pulse_id=10,
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=False,
            source="source-a",
            source_url="http://a.example",
            fetched_at=now.replace(microsecond=0, second=now.second - 10),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.com",
            pulse_id=20,
            pulse_name="Pulse B",
            pulse_created=now,
            is_aggregator=True,
            source="source-b",
            source_url="http://b.example",
            fetched_at=now.replace(microsecond=0, second=now.second - 20),
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="test.org",
            pulse_id=20,
            pulse_name="Pulse B",
            pulse_created=now,
            is_aggregator=True,
            source="source-b",
            source_url="http://b.example",
            fetched_at=now.replace(microsecond=0, second=now.second - 30),
        ),
    ]

    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Insert data
    with TestingSessionLocal() as db:
        db.add_all(sample_data)
        db.commit()

    # ------------------------------------------------------------------ #
    # FastAPI app wiring
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------ #
    # Execute request and validate response
    # ------------------------------------------------------------------ #
    resp = client.get("/api/threatintel/pulses")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert payload["total"] == 4, "total mismatch"
    assert len(payload["by_pulse"]) == 2, "by_pulse length mismatch"

    # rows must be ordered newest first (by fetched_at desc)
    fetched_times = [datetime.fromisoformat(r["fetched_at"]) for r in payload["rows"]]
    assert fetched_times == sorted(fetched_times, reverse=True), "rows not ordered"

    print("PASS")