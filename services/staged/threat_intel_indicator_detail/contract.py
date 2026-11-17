"""Acceptance self-test for the threat_intel_indicator_detail service."""

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

    session = TestingSession()
    first = ThreatIntelRef(
        indicator_type="domain",
        indicator_value="example-one.test",
        pulse_id="pulse-001",
        pulse_name="Example Pulse",
        pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        is_aggregator=False,
        source="example-source",
        source_url="https://example.test/one",
        fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    second = ThreatIntelRef(
        indicator_type="ipv4",
        indicator_value="198.51.100.10",
        pulse_id="pulse-002",
        pulse_name="Second Pulse",
        pulse_created=datetime(2024, 2, 1, tzinfo=timezone.utc),
        is_aggregator=True,
        source="second-source",
        source_url="https://example.test/two",
        fetched_at=datetime(2024, 2, 2, tzinfo=timezone.utc),
    )
    session.add(first)
    session.add(second)
    session.commit()
    first_id = first.id
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

    response = client.get(f"/api/threat-intel/indicators/{first_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["indicator_value"] == "example-one.test", body
    expected_fields = {
        "id",
        "indicator_type",
        "indicator_value",
        "pulse_id",
        "pulse_name",
        "pulse_created",
        "is_aggregator",
        "source",
        "source_url",
        "fetched_at",
    }
    assert expected_fields.issubset(body), body

    missing_response = client.get("/api/threat-intel/indicators/999999999")
    assert missing_response.status_code == 404, missing_response.text
    assert missing_response.json() == {"error": "not_found"}, missing_response.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)