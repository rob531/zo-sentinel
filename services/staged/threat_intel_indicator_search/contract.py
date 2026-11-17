"""Acceptance self-test for the threat-intel indicator search service."""
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
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-ip",
                    pulse_name="IP pulse",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.test/ip",
                    fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-domain",
                    pulse_name="Domain pulse",
                    pulse_created=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="seed",
                    source_url="https://example.test/domain",
                    fetched_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="ip",
                    indicator_value="198.51.100.1",
                    pulse_id="pulse-aggregator",
                    pulse_name="Aggregator pulse",
                    pulse_created=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="seed",
                    source_url="https://example.test/aggregator",
                    fetched_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()
    finally:
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

    with TestClient(app) as client:
        response = client.get("/api/threat-indicators")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 3, body
        assert len(body["items"]) == 3, body

        response = client.get(
            "/api/threat-indicators",
            params={"exclude_aggregators": "true"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2, body
        assert all(not item["is_aggregator"] for item in body["items"]), body

        response = client.get(
            "/api/threat-indicators",
            params={"indicator_type": "does-not-exist"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 0, body
        assert body["items"] == [], body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)