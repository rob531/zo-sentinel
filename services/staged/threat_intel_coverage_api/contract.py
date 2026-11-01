"""Acceptance self-test for the threat intelligence coverage API."""
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
    from app.models import ThreatIntelRef

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)

    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    session = testing_session()
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Example pulse 1",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://alpha.example/pulse-1",
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-1",
                    pulse_name="Example pulse 1",
                    pulse_created=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/pulse-1",
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.3",
                    pulse_id="pulse-2",
                    pulse_name="Example pulse 2",
                    pulse_created=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="beta",
                    source_url="https://beta.example/pulse-2",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id="pulse-2",
                    pulse_name="Example pulse 2",
                    pulse_created=datetime(2024, 1, 4, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/pulse-2",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.com",
                    pulse_id="pulse-3",
                    pulse_name="Example pulse 3",
                    pulse_created=datetime(2024, 1, 5, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/pulse-3",
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

    with TestClient(app) as client:
        response = client.get("/api/threat-intel/coverage")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5, body
    assert len(body["by_indicator_type"]) == 2, body
    assert sum(row["count"] for row in body["by_source"]) == 5, body
    assert isinstance(body["generated_at"], str), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)