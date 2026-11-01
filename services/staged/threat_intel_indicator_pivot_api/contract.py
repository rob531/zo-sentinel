"""Acceptance self-test for the threat intelligence indicator pivot API."""
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Example Pulse",
                source="example",
                source_url="https://example.test/1",
                is_aggregator=True,
                fetched_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-2",
                pulse_name="Another Pulse",
                source="example",
                source_url="https://example.test/2",
                is_aggregator=False,
                fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-3",
                pulse_name="Domain Pulse",
                source="other",
                source_url="https://other.test/3",
                is_aggregator=False,
                fetched_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
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

    response = client.get("/api/threat-intel/indicators")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3, body
    assert body["returned"] == 3, body
    assert body["counts_by_type"]["ip"] == 2, body
    assert body["counts_by_type"]["domain"] == 1, body
    assert body["aggregator_count"] == 1, body

    response = client.get(
        "/api/threat-intel/indicators",
        params={"indicator_type": "ip"},
    )
    assert response.status_code == 200, response.text
    filtered_body = response.json()
    assert filtered_body["returned"] == 2, filtered_body
    assert all(
        indicator["indicator_type"] == "ip"
        for indicator in filtered_body["indicators"]
    ), filtered_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)