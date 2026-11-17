"""Acceptance self-test for the threat intelligence pulse mix service."""
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
    from app.models import ThreatIntelRef

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    old_fetched_at = now - timedelta(days=10)
    recent_fetched_at = now - timedelta(days=1)

    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-a-1",
                pulse_name="Alpha Pulse",
                pulse_created=now - timedelta(days=2),
                source="alpha",
                source_url="https://alpha.example/pulse-a-1",
                is_aggregator=False,
                fetched_at=recent_fetched_at,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-a-2",
                pulse_name="Alpha Pulse",
                pulse_created=now - timedelta(days=1),
                source="alpha",
                source_url="https://alpha.example/pulse-a-2",
                is_aggregator=False,
                fetched_at=old_fetched_at,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example-one.test",
                pulse_id="pulse-b-1",
                pulse_name="Bravo Pulse",
                pulse_created=now - timedelta(days=3),
                source="bravo",
                source_url="https://bravo.example/pulse-b-1",
                is_aggregator=True,
                fetched_at=old_fetched_at,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example-two.test",
                pulse_id="pulse-b-2",
                pulse_name="Bravo Pulse",
                pulse_created=now - timedelta(hours=6),
                source="bravo",
                source_url="https://bravo.example/pulse-b-2",
                is_aggregator=False,
                fetched_at=recent_fetched_at,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.1",
                pulse_id="pulse-a-3",
                pulse_name="Alpha Pulse",
                pulse_created=now - timedelta(hours=3),
                source="alpha",
                source_url="https://alpha.example/pulse-a-3",
                is_aggregator=False,
                fetched_at=old_fetched_at,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example-three.test",
                pulse_id="pulse-b-3",
                pulse_name="Bravo Pulse",
                pulse_created=now - timedelta(days=1),
                source="bravo",
                source_url="https://bravo.example/pulse-b-3",
                is_aggregator=False,
                fetched_at=recent_fetched_at,
            ),
        ]
    )
    session.commit()
    session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/threat-intel/pulse-mix", params={"limit": 50})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 6, body
    assert len(body["by_indicator_type"]) == 2, body
    assert abs(body["aggregator_ratio"] - (1 / 6)) <= 1e-6, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)