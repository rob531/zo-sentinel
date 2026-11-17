"""Acceptance self-test for the freshness_surface_inventory service."""
from __future__ import annotations

import json
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
    try:
        session.add_all(
            [
                FreshnessStat(
                    surface="fresh_surface",
                    payload=json.dumps({"kind": "fresh"}),
                    computed_at=now - timedelta(minutes=5),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="stale_surface",
                    payload=json.dumps({"kind": "stale"}),
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="expired_surface",
                    payload=json.dumps({"kind": "expired"}),
                    computed_at=now - timedelta(days=3),
                    compute_seconds=2.5,
                ),
            ]
        )
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
    response = client.get("/api/freshness/surfaces")
    assert response.status_code == 200, response.text

    body = response.json()
    surfaces = body["surfaces"]
    summary = body["summary"]
    assert len(surfaces) == 3, body
    assert summary["total"] == 3, body
    assert summary["fresh"] == 1, body
    assert summary["stale"] == 1, body
    assert summary["expired"] == 1, body
    assert summary["oldest_age_seconds"] >= 3 * 24 * 60 * 60, body

    expired = next(item for item in surfaces if item["surface"] == "expired_surface")
    assert expired["status"] == "expired", body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)