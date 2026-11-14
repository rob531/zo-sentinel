"""Acceptance self-test for the threat intel reference coverage service."""
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
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    now = datetime.now(timezone.utc)
    session = testing_session()
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id=1001,
                    pulse_name="Pulse IPv4 One",
                    pulse_created=now - timedelta(minutes=5),
                    is_aggregator=False,
                    source="otx",
                    source_url="https://example.test/otx/1",
                    fetched_at=now - timedelta(minutes=1),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.2",
                    pulse_id=1002,
                    pulse_name="Pulse IPv4 Two",
                    pulse_created=now - timedelta(minutes=4),
                    is_aggregator=False,
                    source="otx",
                    source_url="https://example.test/otx/2",
                    fetched_at=now - timedelta(minutes=2),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.3",
                    pulse_id=1003,
                    pulse_name="Pulse IPv4 Aggregator",
                    pulse_created=now - timedelta(minutes=3),
                    is_aggregator=True,
                    source="otx",
                    source_url="https://example.test/otx/3",
                    fetched_at=now - timedelta(minutes=3),
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id=1004,
                    pulse_name="Pulse Domain One",
                    pulse_created=now - timedelta(minutes=2),
                    is_aggregator=False,
                    source="urlscan",
                    source_url="https://example.test/urlscan/4",
                    fetched_at=now - timedelta(minutes=4),
                ),
                ThreatIntelRef(
                    id=5,
                    indicator_type="domain",
                    indicator_value="example.net",
                    pulse_id=1005,
                    pulse_name="Pulse Domain Two",
                    pulse_created=now - timedelta(minutes=1),
                    is_aggregator=False,
                    source="urlscan",
                    source_url="https://example.test/urlscan/5",
                    fetched_at=now - timedelta(minutes=5),
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
    response = client.get("/api/threat-intel/refs/coverage")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_refs"] == 5, body
    assert len(body["by_type"]) == 2, body

    ipv4 = next(item for item in body["by_type"] if item["indicator_type"] == "ipv4")
    assert ipv4["aggregator_count"] == 1, body
    assert len(body["latest"]) == 5, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)