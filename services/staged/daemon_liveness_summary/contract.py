"""Acceptance self-test for the daemon liveness summary service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, JSON, MetaData, String, Table, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry  # noqa: F401
    from app.models import FreshnessStat  # noqa: F401

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    metadata = MetaData()
    service_health = Table(
        "service_health",
        metadata,
        Column("service", String, primary_key=True),
        Column("last_heartbeat", DateTime(timezone=True)),
        Column("status", String, nullable=False),
        Column("meta", JSON),
    )
    service_health.create(eng, checkfirst=True)

    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    now = datetime.now(timezone.utc)

    session = TestingSession()
    session.execute(
        insert(service_health).values(
            [
                {
                    "service": "fresh-service",
                    "last_heartbeat": now - timedelta(seconds=10),
                    "status": "healthy",
                    "meta": {"kind": "collector"},
                },
                {
                    "service": "stale-service",
                    "last_heartbeat": now - timedelta(seconds=900),
                    "status": "healthy",
                    "meta": None,
                },
                {
                    "service": "old-service",
                    "last_heartbeat": now - timedelta(seconds=7200),
                    "status": "healthy",
                    "meta": {"kind": "collector"},
                },
            ]
        )
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
    response = client.get("/api/ops/daemon-liveness")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 3, body
    assert body["healthy"] == 1, body
    assert body["stale"] == 1, body
    assert body["down"] == 1, body

    services = body["services"]
    assert len(services) == 3, body
    ages = [item["age_seconds"] for item in services]
    assert ages == sorted(ages, reverse=True), body
    assert services[0]["service"] == "old-service", body
    assert services[1]["service"] == "stale-service", body
    assert services[2]["service"] == "fresh-service", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)