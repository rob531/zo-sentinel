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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = testing_session()
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-old",
                    pulse_name="Old pulse",
                    is_aggregator=False,
                    source="legacy-feed",
                    source_url="https://intel.example/old",
                    fetched_at=now - timedelta(days=90),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.com",
                    pulse_id="pulse-recent-1",
                    pulse_name="Recent pulse 1",
                    is_aggregator=True,
                    source="vendor-a",
                    source_url="https://intel.example/recent-1",
                    fetched_at=now - timedelta(days=1),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://example.org/path",
                    pulse_id="pulse-recent-2",
                    pulse_name="Recent pulse 2",
                    is_aggregator=False,
                    source="vendor-b",
                    source_url="https://intel.example/recent-2",
                    fetched_at=now - timedelta(days=2),
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="a" * 64,
                    pulse_id="pulse-recent-3",
                    pulse_name="Recent pulse 3",
                    is_aggregator=False,
                    source="vendor-a",
                    source_url="https://intel.example/recent-3",
                    fetched_at=now - timedelta(hours=1),
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
    response = client.get("/api/threat-intel/refs/freshness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_refs"] == 4, body
    assert body["stale_refs"] == 1, body
    assert body["aggregator_refs"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)