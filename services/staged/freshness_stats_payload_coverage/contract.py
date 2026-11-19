"""Acceptance self-test for the freshness stats payload coverage service."""
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
    from app.models import Base, FreshnessStat

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
            FreshnessStat(
                surface="empty-payload",
                payload={},
                computed_at=now,
                compute_seconds=1.5,
            ),
            FreshnessStat(
                surface="stale-surface",
                payload={"status": "ok"},
                computed_at=now - timedelta(hours=48),
                compute_seconds=2.5,
            ),
            FreshnessStat(
                surface="fresh-one",
                payload={"status": "ok"},
                computed_at=now - timedelta(hours=2),
                compute_seconds=3.5,
            ),
            FreshnessStat(
                surface="fresh-two",
                payload={"status": "ok"},
                computed_at=now - timedelta(hours=4),
                compute_seconds=4.5,
            ),
        ]
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
    response = client.get("/api/freshness/payload-coverage")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["totals"]["surface_count"] == 4, body

    surfaces = {item["surface"]: item for item in body["surfaces"]}
    assert "stale-surface" in surfaces, body
    assert surfaces["stale-surface"]["stale"] is True, body
    assert surfaces["empty-payload"]["has_payload"] is False, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)