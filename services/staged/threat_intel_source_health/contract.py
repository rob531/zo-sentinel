"""Acceptance self-test for the threat_intel_source_health service."""
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
                id=1,
                indicator_type="ipv4",
                indicator_value="192.0.2.10",
                pulse_id="pulse-alpha-1",
                pulse_name="Alpha pulse",
                pulse_created=now,
                is_aggregator=False,
                source="alpha",
                source_url="https://alpha.example/feed",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="alpha.example",
                pulse_id="pulse-alpha-2",
                pulse_name="Alpha pulse",
                pulse_created=now,
                is_aggregator=False,
                source="alpha",
                source_url="https://alpha.example/feed",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="ipv4",
                indicator_value="198.51.100.20",
                pulse_id="pulse-aggregator-1",
                pulse_name="Aggregator pulse",
                pulse_created=now,
                is_aggregator=True,
                source="aggregator",
                source_url="https://aggregator.example/feed",
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
    response = client.get("/api/threat-intel/sources")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_sources"] == 2, body
    assert len(body["sources"]) == 2, body

    sources = {row["source"]: row for row in body["sources"]}
    assert set(sources) == {"alpha", "aggregator"}, body
    assert sources["alpha"]["indicators"] == 2, body
    assert sources["alpha"]["pulses"] == 2, body
    assert sources["alpha"]["is_aggregator"] is False, body
    assert sources["aggregator"]["indicators"] == 1, body
    assert sources["aggregator"]["pulses"] == 1, body
    assert sources["aggregator"]["is_aggregator"] is True, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)