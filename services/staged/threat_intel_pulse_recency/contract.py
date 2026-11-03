"""Acceptance self-test for the threat-intel pulse-recency service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta, timezone

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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    stale_at = now - timedelta(days=120)

    session = testing_session()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-fresh-1",
                pulse_name="Fresh Pulse 1",
                pulse_created=now,
                is_aggregator=False,
                source="test",
                source_url="https://example.test/pulse-fresh-1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-fresh-2",
                pulse_name="Fresh Pulse 2",
                pulse_created=now,
                is_aggregator=True,
                source="test",
                source_url="https://example.test/pulse-fresh-2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="hash",
                indicator_value="a" * 64,
                pulse_id="pulse-stale",
                pulse_name="Stale Pulse",
                pulse_created=stale_at,
                is_aggregator=False,
                source="test",
                source_url="https://example.test/pulse-stale",
                fetched_at=stale_at,
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

    response = TestClient(app).get(
        "/api/threat-intel/pulse-recency",
        params={"days": 90},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_refs"] == 3, body
    assert body["stale_pulse_count"] == 1, body
    assert "pulse-stale" in {pulse["pulse_id"] for pulse in body["pulses"]}, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)