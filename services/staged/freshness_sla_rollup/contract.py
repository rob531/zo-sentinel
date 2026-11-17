"""Acceptance self-test for the freshness SLA rollup service."""
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
    from app.models import Base, FreshnessStat

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
    session.add(
        FreshnessStat(
            surface="fresh_surface",
            payload={"source": "fresh"},
            computed_at=now - timedelta(seconds=60),
            compute_seconds=60.0,
        )
    )
    session.add(
        FreshnessStat(
            surface="stale_surface",
            payload={"source": "stale"},
            computed_at=now - timedelta(seconds=7200),
            compute_seconds=7200.0,
        )
    )
    session.add(
        FreshnessStat(
            surface="filtered_surface",
            payload={"source": "filtered"},
            computed_at=now - timedelta(seconds=120),
            compute_seconds=120.0,
        )
    )
    session.commit()
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
    response = client.get("/api/freshness/sla-rollup")
    assert response.status_code == 200, response.text

    body = response.json()
    surfaces = body["surfaces"]
    assert len(surfaces) == 3, body
    status_by_surface = {item["surface"]: item["status"] for item in surfaces}
    assert status_by_surface["stale_surface"] == "stale", body
    assert status_by_surface["fresh_surface"] == "fresh", body
    assert body["counts"]["stale"] >= 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)