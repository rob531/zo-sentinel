"""Acceptance self-test for the threat-intel indicator decay API."""
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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    testing_session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = testing_session()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-aggregator",
                pulse_name="Aggregator Pulse",
                pulse_created=now,
                is_aggregator=True,
                source="seed",
                source_url="https://example.test/intel/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-aggregator",
                pulse_name="Aggregator Pulse",
                pulse_created=now,
                is_aggregator=True,
                source="seed",
                source_url="https://example.test/intel/2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.org",
                pulse_id="pulse-standard",
                pulse_name="Standard Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="seed",
                source_url="https://example.test/intel/3",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.net",
                pulse_id="pulse-standard",
                pulse_name="Standard Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="seed",
                source_url="https://example.test/intel/4",
                fetched_at=now,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/threat-intel/indicators/decay")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4, body
    assert len(body["by_type"]) == 2, body
    assert any(pulse["is_aggregator"] is True for pulse in body["pulses"]), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)