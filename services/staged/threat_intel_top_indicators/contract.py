"""Acceptance self-test for the threat intelligence top-indicators service."""
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
    from app.models import ThreatIntelRef

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now - timedelta(days=3),
                is_aggregator=False,
                source="internal",
                source_url="https://intel.example/pulse-1",
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=now - timedelta(days=1),
                is_aggregator=True,
                source="aggregator",
                source_url="https://intel.example/pulse-2",
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.99",
                pulse_id="pulse-3",
                pulse_name="Pulse Three",
                pulse_created=now - timedelta(days=2),
                is_aggregator=False,
                source="internal",
                source_url="https://intel.example/pulse-3",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="malicious.example",
                pulse_id="pulse-4",
                pulse_name="Pulse Four",
                pulse_created=now - timedelta(days=4),
                is_aggregator=False,
                source="internal",
                source_url="https://intel.example/pulse-4",
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
    response = client.get("/api/threat-intel/top-indicators")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "types" in body, body
    by_type = {item["indicator_type"]: item for item in body["types"]}
    assert set(by_type) == {"ip", "domain"}, body

    ip_type = by_type["ip"]
    domain_type = by_type["domain"]
    assert ip_type["count"] == 3, body
    assert ip_type["distinct_values"] == 2, body
    assert domain_type["count"] == 1, body
    assert domain_type["distinct_values"] == 1, body

    top_values = {item["value"]: item["count"] for item in ip_type["top_values"]}
    assert top_values["192.0.2.10"] == 2, body
    assert top_values["192.0.2.99"] == 1, body
    assert ip_type["is_aggregator_count"] == 1, body
    assert domain_type["is_aggregator_count"] == 0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)