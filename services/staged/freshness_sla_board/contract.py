"""Acceptance self-test for the freshness SLA board service."""
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
    from app.models import Base, FreshnessStat, McpServerRegistry

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
            McpServerRegistry(
                server_id="srv-fresh-1",
                name="fresh-one",
                last_seen=now - timedelta(hours=1),
                first_seen=now - timedelta(hours=2),
            ),
            McpServerRegistry(
                server_id="srv-fresh-2",
                name="fresh-two",
                last_seen=now - timedelta(hours=8),
                first_seen=now - timedelta(hours=10),
            ),
            McpServerRegistry(
                server_id="srv-fresh-3",
                name="fresh-three",
                last_seen=now - timedelta(hours=30),
                first_seen=now - timedelta(hours=40),
            ),
            FreshnessStat(
                surface="web",
                payload={
                    "server_ids": ["srv-fresh-1", "srv-fresh-2"],
                    "servers": [
                        {"server_id": "srv-fresh-1"},
                        {"server_id": "srv-fresh-2"},
                    ],
                    "server_count": 2,
                },
                computed_at=now,
                compute_seconds=0.5,
            ),
            FreshnessStat(
                surface="api",
                payload={
                    "server_ids": ["srv-fresh-3"],
                    "servers": [{"server_id": "srv-fresh-3"}],
                    "server_count": 1,
                },
                computed_at=now,
                compute_seconds=0.25,
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

    response = TestClient(app).get("/api/freshness/sla")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "surfaces" in body, body
    surfaces = {entry["surface"]: entry for entry in body["surfaces"]}
    assert "web" in surfaces, body
    assert "api" in surfaces, body
    assert any(entry["sla_ok"] is False for entry in surfaces.values()), body
    assert all(
        entry["p90_age_hours"] >= entry["median_age_hours"]
        for entry in surfaces.values()
    ), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)