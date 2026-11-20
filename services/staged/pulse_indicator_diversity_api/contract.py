"""Acceptance self-test for the pulse indicator diversity API."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, ThreatIntelRef

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse 1",
                is_aggregator=True,
                source="alpha",
                source_url="https://alpha.example/1",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.org",
                pulse_id="pulse-2",
                pulse_name="Pulse 2",
                is_aggregator=False,
                source="alpha",
                source_url="https://alpha.example/2",
            ),
            ThreatIntelRef(
                indicator_type="url",
                indicator_value="https://beta.example/3",
                pulse_id="pulse-1",
                pulse_name="Pulse 1",
                is_aggregator=False,
                source="beta",
                source_url="https://beta.example/3",
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.4",
                pulse_id="pulse-2",
                pulse_name="Pulse 2",
                is_aggregator=False,
                source="beta",
                source_url="https://beta.example/4",
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
    response = client.get("/api/threat-intel/pulse-diversity")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_indicators"] == 4, body
    assert body["distinct_indicator_types"] == 3, body
    assert body["distinct_pulses"] == 2, body
    assert body["aggregator_indicator_count"] == 1, body
    assert len(body["by_source"]) == 2, body
    assert len(body["by_indicator_type"]) == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)