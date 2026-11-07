"""Acceptance self-test for the freshness_stat_reader service."""
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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    s = TestingSession()
    s.add(
        FreshnessStat(
            surface="old_surface",
            payload={"state": "old"},
            computed_at=now - timedelta(seconds=7200),
            compute_seconds=12,
        )
    )
    s.add(
        FreshnessStat(
            surface="fresh_surface_one",
            payload={"state": "fresh"},
            computed_at=now - timedelta(seconds=60),
            compute_seconds=4,
        )
    )
    s.add(
        FreshnessStat(
            surface="fresh_surface_two",
            payload={"state": "fresh"},
            computed_at=now,
            compute_seconds=3,
        )
    )
    s.commit()
    s.close()

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
    response = client.get("/api/freshness/surfaces")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["count"] == 3, body
    assert len(body["surfaces"]) == 3, body

    stale = [surface for surface in body["surfaces"] if surface["stale"]]
    assert len(stale) == 1, body
    assert stale[0]["age_seconds"] > 0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)