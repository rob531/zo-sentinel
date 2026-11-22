"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Run: python -m services.staged.app3t_perspective_unseen_events_api.contract
"""
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
    from app.models import PerspectiveEvent

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(eng, checkfirst=True)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    seeded_events = [
        PerspectiveEvent(
            id=101,
            perspective_id="perspective-a",
            server_id="server-a",
            change_type="TIER_CHANGED",
            old_tier="LOW",
            new_tier="HIGH",
            seen=True,
            created_at=now - timedelta(hours=4),
        ),
        PerspectiveEvent(
            id=102,
            perspective_id="perspective-b",
            server_id="server-b",
            change_type="TIER_CHANGED",
            old_tier="HIGH",
            new_tier="LOW",
            seen=True,
            created_at=now - timedelta(hours=3),
        ),
        PerspectiveEvent(
            id=103,
            perspective_id="perspective-a",
            server_id="server-c",
            change_type="ADDED",
            old_tier=None,
            new_tier="HIGH",
            seen=False,
            created_at=now - timedelta(hours=2),
        ),
        PerspectiveEvent(
            id=104,
            perspective_id="perspective-b",
            server_id="server-d",
            change_type="REMOVED",
            old_tier="LOW",
            new_tier=None,
            seen=False,
            created_at=now - timedelta(hours=1),
        ),
    ]

    session = TestingSession()
    session.add_all(seeded_events)
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
    response = client.get("/api/perspectives/unseen-events")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["unseen_count"] == 2, body
    events = body["events"]
    assert len(events) == 2, body

    returned_ids = {event["id"] for event in events}
    assert returned_ids == {103, 104}, body

    db = TestingSession()
    try:
        for event in events:
            stored = db.get(PerspectiveEvent, event["id"])
            assert stored is not None
            assert stored.seen is False, event
    finally:
        db.close()

    created_values = [event["created_at"] for event in events]
    assert created_values == sorted(created_values, reverse=True), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)