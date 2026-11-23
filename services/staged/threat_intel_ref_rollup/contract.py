"""Acceptance self-test for the threat-intel reference rollup service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, ThreatIntelRef

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="First pulse",
                pulse_created=now - timedelta(days=2),
                is_aggregator=True,
                source="source-a",
                source_url="https://source-a.example/pulse-1",
                fetched_at=now - timedelta(days=1),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.org",
                pulse_id="pulse-1",
                pulse_name="First pulse",
                pulse_created=now - timedelta(days=2),
                is_aggregator=False,
                source="source-a",
                source_url="https://source-a.example/pulse-1",
                fetched_at=now - timedelta(days=3),
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.2",
                pulse_id="pulse-2",
                pulse_name="Second pulse",
                pulse_created=now - timedelta(days=1),
                is_aggregator=True,
                source="source-b",
                source_url="https://source-b.example/pulse-2",
                fetched_at=now - timedelta(days=5),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.net",
                pulse_id="pulse-2",
                pulse_name="Second pulse",
                pulse_created=now - timedelta(days=1),
                is_aggregator=False,
                source="source-b",
                source_url="https://source-b.example/pulse-2",
                fetched_at=now - timedelta(days=7),
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/threat-intel/rollup")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["window_days"] == 30, body
    assert body["total_refs"] == 4, body
    assert len(body["by_indicator_type"]) >= 1, body
    assert body["distinct_pulses"] == 2, body
    assert body["aggregator_refs"] + body["direct_refs"] == 4, body
    assert len(body["recent_pulses"]) == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)