"""Acceptance self-test for the threat intelligence pulse-age service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta, timezone

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
    ages = [timedelta(hours=12), timedelta(days=3), timedelta(days=10), timedelta(days=100)]
    session = testing_session()
    for index, age in enumerate(ages, start=1):
        session.add(
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value=f"192.0.2.{index}",
                pulse_id=f"pulse-{index}",
                pulse_name=f"Pulse {index}",
                pulse_created=now - age,
                is_aggregator=index == 4,
                source="contract-test",
                source_url=f"https://example.test/pulses/{index}",
                fetched_at=now - age,
            )
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
    response = client.get("/api/threat_intel/pulse_age", params={"days": 365})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_refs"] == len(ages), body
    assert body["aggregator_refs"] == 1, body

    buckets = {item["bucket"]: item["count"] for item in body["buckets"]}
    assert buckets.get("90d+") == 1, body
    assert body["top_pulses"], body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)