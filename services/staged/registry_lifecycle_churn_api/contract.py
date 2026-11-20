"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.<stage>.<name>.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity). A
non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not
a silent skip. That is the deliberate inverse of the 74% Tier-0 degradation
(FU-031) that let presence pass for correctness.

Run:  python -m services.staged.registry_lifecycle_churn_api.contract
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
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    old = now - timedelta(days=40)
    recent = now - timedelta(days=2)

    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="lifecycle-new",
                    first_seen=recent,
                    last_seen=recent,
                    last_scanned=recent,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="lifecycle-dormant",
                    first_seen=old,
                    last_seen=old,
                    last_scanned=old,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="lifecycle-stale-active",
                    first_seen=old,
                    last_seen=recent,
                    last_scanned=old,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="lifecycle-churned",
                    first_seen=old,
                    last_seen=recent,
                    last_scanned=recent,
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="lifecycle-active",
                    first_seen=old,
                    last_seen=recent,
                    last_scanned=recent,
                    scan_count=1,
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
    response = client.get("/api/registry/lifecycle-churn")
    assert response.status_code == 200, response.text

    body = response.json()
    buckets = body["buckets"]
    assert buckets["new"] == 1, body
    assert buckets["dormant"] == 1, body
    assert buckets["stale_active"] == 1, body
    assert buckets["churned"] == 1, body
    assert buckets["active"] == 1, body
    assert body["total_servers"] == 5, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)