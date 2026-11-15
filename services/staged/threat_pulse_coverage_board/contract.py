"""Contract acceptance self-test for threat_pulse_coverage_board."""
from __future__ import annotations

import sys
from datetime import datetime, timezone


def run() -> bool:
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

    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=created,
                is_aggregator=True,
                source="seed",
                source_url="https://example.test/pulse-1",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example-one.test",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=created,
                is_aggregator=True,
                source="seed",
                source_url="https://example.test/pulse-1",
            ),
            ThreatIntelRef(
                indicator_type="hash",
                indicator_value="a" * 64,
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=created,
                is_aggregator=True,
                source="seed",
                source_url="https://example.test/pulse-1",
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=created,
                is_aggregator=False,
                source="seed",
                source_url="https://example.test/pulse-2",
            ),
            ThreatIntelRef(
                indicator_type="url",
                indicator_value="https://example-two.test/path",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=created,
                is_aggregator=False,
                source="seed",
                source_url="https://example.test/pulse-2",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/threat/pulses")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["pulses"]) == 2, body
    assert body["totals"]["indicator_count"] == 5, body
    assert body["totals"]["aggregator_count"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)