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
    from app.models import Base, PerspectiveEvent, PerspectiveSnapshot

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    now = datetime.utcnow()
    recent = now - timedelta(days=1)
    far_past = now - timedelta(days=365)

    session = testing_session()
    try:
        session.add_all(
            [
                PerspectiveSnapshot(
                    perspective_id="perspective-1",
                    taken_at=recent,
                    membership={"server_ids": ["server-1"]},
                ),
                PerspectiveSnapshot(
                    perspective_id="perspective-1",
                    taken_at=now - timedelta(hours=12),
                    membership={"server_ids": ["server-1", "server-2"]},
                ),
                PerspectiveSnapshot(
                    perspective_id="perspective-2",
                    taken_at=far_past,
                    membership={"server_ids": ["server-3"]},
                ),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="tier_changed",
                    seen=False,
                    created_at=now - timedelta(hours=10),
                ),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-2",
                    change_type="membership_changed",
                    seen=False,
                    created_at=now - timedelta(hours=8),
                ),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="tier_changed",
                    seen=True,
                    created_at=now - timedelta(hours=6),
                ),
                PerspectiveEvent(
                    perspective_id="perspective-2",
                    server_id="server-3",
                    change_type="metadata_changed",
                    seen=True,
                    created_at=far_past + timedelta(days=1),
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
    response = client.get("/api/perspectives/snapshot-ageing", params={"days": 7})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["window_days"] == 7, body
    assert body["stale_count"] >= 1, body

    perspectives = {item["perspective_id"]: item for item in body["perspectives"]}
    assert perspectives["perspective-1"]["unseen_events"] == 2, body
    assert perspectives["perspective-2"]["unseen_events"] == 0, body
    assert perspectives["perspective-1"]["snapshots_last_n_days"] == 2, body
    assert perspectives["perspective-2"]["stale"] is True, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)