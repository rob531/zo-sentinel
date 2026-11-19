"""Acceptance self-test for the threat-intel reference ageing API."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


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

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ipv4",
                indicator_value="192.0.2.1",
                source="source-a",
                is_aggregator=False,
                fetched_at=now - timedelta(days=1),
                pulse_id="pulse-1",
                source_url="https://example.test/1",
            ),
            ThreatIntelRef(
                indicator_type="ipv4",
                indicator_value="192.0.2.2",
                source="source-b",
                is_aggregator=False,
                fetched_at=now - timedelta(days=10),
                pulse_id="pulse-2",
                source_url="https://example.test/2",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                source="source-a",
                is_aggregator=False,
                fetched_at=now - timedelta(days=45),
                pulse_id="pulse-3",
                source_url="https://example.test/3",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.org",
                source="source-b",
                is_aggregator=True,
                fetched_at=now - timedelta(days=100),
                pulse_id="pulse-4",
                source_url="https://example.test/4",
            ),
        ]
    )
    session.commit()
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

    response = TestClient(app).get("/api/threat-intel/ref-ageing", params={"days": 90})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 90, body
    assert body["total"] == 4, body

    bucket_counts = {item["bucket"]: item["count"] for item in body["buckets"]}
    assert bucket_counts == {
        "0-7d": 1,
        "8-30d": 1,
        "31-90d": 1,
        "90d+": 1,
    }, body
    assert len(body["by_source"]) == 2, body
    assert all(item["count"] >= 1 for item in body["by_source"]), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)