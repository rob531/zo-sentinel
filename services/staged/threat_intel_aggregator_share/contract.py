"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds test data, overrides the DB dependency, and asserts that the
endpoint returns the expected aggregated view.

Usage:
    python -m services.staged.threat_intel_aggregator_share.contract
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    """Boot the service, seed test data, and validate the API contract."""
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy core for an in‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real application DB session getter and ORM models
    from app.db import get_session
    from app.models import Base, ThreatIntelRef

    # Service router
    from .router import router

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite engine and create the schema
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------
    # Seed the test data
    # ------------------------------------------------------------------
    now = datetime.utcnow()
    recent = now - timedelta(days=5)
    old = now - timedelta(days=60)

    session = TestingSession()
    # Row 1 – aggregator source 1
    session.add(
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="1.1.1.1",
            is_aggregator=True,
            pulse_created=recent,
            pulse_id="p1",
            pulse_name="Pulse 1",
            source="AggSource1",
            source_url="https://example.com/agg1",
            fetched_at=now,
        )
    )
    # Row 2 – aggregator source 2
    session.add(
        ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="example.com",
            is_aggregator=True,
            pulse_created=recent,
            pulse_id="p2",
            pulse_name="Pulse 2",
            source="AggSource2",
            source_url="https://example.com/agg2",
            fetched_at=now,
        )
    )
    # Row 3 – non‑aggregator specific source
    session.add(
        ThreatIntelRef(
            id=3,
            indicator_type="ip",
            indicator_value="2.2.2.2",
            is_aggregator=False,
            pulse_created=recent,
            pulse_id="p3",
            pulse_name="Pulse 3",
            source="SpecificSource",
            source_url="https://example.com/spec",
            fetched_at=now,
        )
    )
    # Row 4 – old row outside the default 30‑day window (should be ignored)
    session.add(
        ThreatIntelRef(
            id=4,
            indicator_type="url",
            indicator_value="http://old.com",
            is_aggregator=True,
            pulse_created=old,
            pulse_id="p4",
            pulse_name="Old Pulse",
            source="OldAgg",
            source_url="https://example.com/old",
            fetched_at=old,
        )
    )
    session.commit()
    session.close()

    # ------------------------------------------------------------------
    # Dependency override so the service uses the in‑memory DB
    # ------------------------------------------------------------------
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Build FastAPI app, mount router, and run the test client
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/threat-intel/aggregator-share")  # default days=30
    assert response.status_code == 200, f"Unexpected status: {response.text}"
    body = response.json()

    # ------------------------------------------------------------------
    # Assertions matching the seeded data
    # ------------------------------------------------------------------
    assert body.get("days") == 30, f"days mismatch: {body}"
    assert body.get("total") == 3, f"total mismatch: {body}"

    by_agg = body.get("by_aggregator", {})
    assert by_agg.get("true") == 2, f"by_aggregator true mismatch: {by_agg}"
    assert by_agg.get("false") == 1, f"by_aggregator false mismatch: {by_agg}"

    # by_source list – three distinct sources, each count 1
    src_list = body.get("by_source", [])
    assert isinstance(src_list, list) and len(src_list) == 3, f"by_source length: {src_list}"
    src_map = {entry["source"]: entry for entry in src_list}
    for src, expected_agg in {
        "AggSource1": True,
        "AggSource2": True,
        "SpecificSource": False,
    }.items():
        entry = src_map.get(src)
        assert entry is not None, f"Missing source entry for {src}"
        assert entry["count"] == 1, f"Count mismatch for {src}: {entry}"
        assert entry["is_aggregator"] == expected_agg, f"is_aggregator mismatch for {src}: {entry}"

    # by_indicator_type – must contain at least one entry (ip and domain present)
    ind_list = body.get("by_indicator_type", [])
    assert isinstance(ind_list, list) and len(ind_list) >= 1, f"by_indicator_type empty: {ind_list}"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)