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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Example Pulse One",
                is_aggregator=True,
                source="alpha",
                source_url="https://example.test/one",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-1",
                pulse_name="Example Pulse One",
                is_aggregator=False,
                source="beta",
                source_url="https://example.test/two",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-2",
                pulse_name="Example Pulse Two",
                is_aggregator=False,
                source="beta",
                source_url="https://example.test/three",
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
    response = client.get("/api/threat-intel/refs/rollup")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 3, body
    indicator_counts = {
        item["indicator_type"]: item["count"]
        for item in body["by_indicator_type"]
    }
    assert indicator_counts.get("ip") == 2, body
    assert body["last_fetched_at"] is not None, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)