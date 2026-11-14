"""Acceptance self-test for the threat intelligence pulse source breakdown API."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

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
                pulse_created=now,
                is_aggregator=True,
                source="source-a",
                source_url="https://example.test/source-a",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.org",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now,
                is_aggregator=False,
                source="source-b",
                source_url="https://example.test/source-b",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="hash",
                indicator_value="a" * 64,
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=now,
                is_aggregator=False,
                source="source-c",
                source_url="https://example.test/source-c",
                fetched_at=now,
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
    response = client.get("/api/threat-intel/pulse-sources")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["sources"]) == 3, body
    assert body["total_indicators"] == 3, body

    by_source = {item["source"]: item for item in body["sources"]}
    assert by_source["source-a"]["aggregator_indicators"] == 1, body
    assert by_source["source-a"]["indicator_count"] == 1, body
    assert by_source["source-a"]["distinct_pulses"] == 1, body
    assert body["total_sources"] == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)