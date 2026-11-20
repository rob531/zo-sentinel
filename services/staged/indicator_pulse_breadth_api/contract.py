"""contract.py -- acceptance self‑test for indicator_pulse_breadth_api.

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds test data, invokes the endpoint and validates the response.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, ThreatIntelRef

    from .router import router

    # ------------------------------------------------------------------
    # In‑memory SQLite engine and session factory
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------
    # Seed test data (4 rows, 2 indicator types, 1 aggregator, 1 stale)
    # ------------------------------------------------------------------
    now = datetime.utcnow()
    newest_fetched = now
    stale_fetched = now - timedelta(days=10)  # >7 days older than newest

    rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="1.2.3.4",
            pulse_id="p1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=30),
            is_aggregator=False,
            source="sourceA",
            source_url="https://example.com/a",
            fetched_at=newest_fetched,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="5.6.7.8",
            pulse_id="p2",
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=20),
            is_aggregator=True,
            source="sourceA",
            source_url="https://example.com/a",
            fetched_at=newest_fetched,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.com",
            pulse_id="p3",
            pulse_name="Pulse Three",
            pulse_created=now - timedelta(days=15),
            is_aggregator=False,
            source="sourceB",
            source_url="https://example.com/b",
            fetched_at=newest_fetched,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="stale.com",
            pulse_id="p4",
            pulse_name="Pulse Four",
            pulse_created=now - timedelta(days=5),
            is_aggregator=False,
            source="sourceB",
            source_url="https://example.com/b",
            fetched_at=stale_fetched,
        ),
    ]

    sess = TestingSession()
    sess.add_all(rows)
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------
    # Dependency override for the FastAPI app
    # ------------------------------------------------------------------
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------
    # Execute test request
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/threat-intel/indicator-breadth")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_refs"] == 4, body
    assert len(body["by_indicator_type"]) == 2, body
    assert body["stale_pulse_count"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)