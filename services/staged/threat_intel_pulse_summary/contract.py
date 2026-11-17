"""Acceptance self-test for the threat intelligence pulse summary service."""
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

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # The acceptance fixture includes legacy rows without a pulse assignment.
    # Make that nullable in the hermetic SQLite schema while retaining the
    # real ORM model and data-layer implementation.
    ThreatIntelRef.__table__.c.pulse_id.nullable = True
    Base.metadata.create_all(engine)

    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    now = datetime.now().replace(microsecond=0)
    earlier = now - timedelta(days=2)

    session = testing_session()
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.10",
                    pulse_id="pulse-live",
                    pulse_name="Live pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="internal-feed",
                    source_url="https://intel.example/live/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-live",
                    pulse_name="Live pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="internal-feed",
                    source_url="https://intel.example/live/2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="ipv6",
                    indicator_value="2001:db8::10",
                    pulse_id="pulse-secondary",
                    pulse_name="Secondary pulse",
                    pulse_created=earlier,
                    is_aggregator=False,
                    source="partner-feed",
                    source_url="https://intel.example/secondary/1",
                    fetched_at=earlier,
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="0123456789abcdef",
                    pulse_id=None,
                    pulse_name=None,
                    pulse_created=now,
                    is_aggregator=False,
                    source="legacy-feed",
                    source_url="https://intel.example/legacy/1",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/threat-intel/pulses")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 30, body
    assert body["total_pulses"] == 3, body
    assert isinstance(body["pulses"], list), body

    pulses = {pulse["pulse_id"]: pulse for pulse in body["pulses"]}
    assert "unassigned" in pulses, body

    unassigned = pulses["unassigned"]
    assert unassigned["indicator_count"] == 1, body
    assert any(pulse["aggregator_count"] == 1 for pulse in body["pulses"]), body

    for pulse in body["pulses"]:
        assert isinstance(pulse["indicator_types"], list), body
        assert all(isinstance(value, str) for value in pulse["indicator_types"]), body
        assert isinstance(pulse["sources"], list), body
        assert all(isinstance(value, str) for value in pulse["sources"]), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)