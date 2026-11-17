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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Recent indicators",
            source="feed-a",
            source_url="https://feed-a.example/1",
            fetched_at=now - timedelta(hours=12),
            is_aggregator=False,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-2",
            pulse_name="Older indicators",
            source="feed-a",
            source_url="https://feed-a.example/2",
            fetched_at=now - timedelta(days=40),
            is_aggregator=False,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="one.example",
            pulse_id="pulse-3",
            pulse_name="Domain indicators",
            source="feed-b",
            source_url="https://feed-b.example/3",
            fetched_at=now - timedelta(days=5),
            is_aggregator=False,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="two.example",
            pulse_id="pulse-4",
            pulse_name="Domain indicators",
            source="feed-b",
            source_url="https://feed-b.example/4",
            fetched_at=now - timedelta(days=5),
            is_aggregator=False,
        ),
    ]

    session = TestingSession()
    try:
        session.add_all(rows)
        session.commit()
    finally:
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
    response = client.get(
        "/api/threat-intel/indicator-age",
        params={"max_age_days": 30},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_indicators"] == 4, body
    assert body["stale_count"] == 1, body
    assert body["groups"], body

    expected_bands = {"0-1", "2-7", "8-30", "31-90", "90+"}
    assert sum(group["stale_count"] for group in body["groups"]) == 1, body
    for group in body["groups"]:
        assert expected_bands.issubset(group["age_bands"]), group

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)