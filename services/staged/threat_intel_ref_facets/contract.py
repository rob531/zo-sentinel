"""Acceptance self-test for the threat-intel reference facets service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


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

    now = datetime.now(timezone.utc)
    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now - timedelta(days=2),
                is_aggregator=False,
                source="feed-a",
                source_url="https://feed-a.example/1",
                fetched_at=now - timedelta(days=1),
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now - timedelta(days=2),
                is_aggregator=False,
                source="feed-a",
                source_url="https://feed-a.example/2",
                fetched_at=now - timedelta(days=1),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example-one.test",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=now - timedelta(days=1),
                is_aggregator=True,
                source="feed-b",
                source_url="https://feed-b.example/1",
                fetched_at=now - timedelta(days=1),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example-two.test",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=now - timedelta(days=1),
                is_aggregator=True,
                source="feed-b",
                source_url="https://feed-b.example/2",
                fetched_at=now - timedelta(days=1),
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
    response = client.get("/api/threat-intel/facets")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_rows"] == 4, body
    assert any(
        item["key"] == "ip" and item["count"] == 2
        for item in body["by_indicator_type"]
    ), body
    assert body["distinct_pulses"] == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)