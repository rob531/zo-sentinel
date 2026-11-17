"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.staged.scan_freshness_sla_api.contract` -- and a zero exit is
the proof that the service boots, mounts, serves 200, and returns a schema-valid
body over the REAL data layer (overridden to an in-memory SQLite for hermeticity).
A non-zero exit keeps the service in staged/.

Run:  python -m services.staged.scan_freshness_sla_api.contract   ->  prints PASS / FAIL, exits 0/1
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
    from app.models import McpServerRegistry

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(eng, checkfirst=True)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    s = TestingSession()
    s.add(
        McpServerRegistry(
            server_id="fresh-server",
            name="Fresh Server",
            registry_source="github",
            trust_score=0.95,
            verdict="TRUSTED",
            first_seen=now - timedelta(hours=1),
            last_seen=now - timedelta(minutes=5),
            last_assessed=now - timedelta(hours=1),
            last_scanned=now - timedelta(hours=1),
            scan_count=1,
            risk_tier="LOW",
            url="https://example.test/fresh",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="stale-server",
            name="Stale Server",
            registry_source="github",
            trust_score=0.70,
            verdict="TRUSTED",
            first_seen=now - timedelta(hours=101),
            last_seen=now - timedelta(days=4),
            last_assessed=now - timedelta(hours=100),
            last_scanned=now - timedelta(hours=100),
            scan_count=4,
            risk_tier="MEDIUM",
            url="https://example.test/stale",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="never-scanned-server",
            name="Never Scanned Server",
            registry_source="npm",
            trust_score=0.50,
            verdict="UNKNOWN",
            first_seen=now - timedelta(hours=2),
            last_seen=now - timedelta(hours=2),
            last_assessed=now - timedelta(hours=2),
            last_scanned=None,
            scan_count=0,
            risk_tier="HIGH",
            url="https://example.test/never-scanned",
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

    c = TestClient(app)

    response = c.get("/api/servers/freshness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_servers"] == 3, body
    assert sum(body["buckets"].values()) == 3, body
    assert body["buckets"]["never_scanned"] == 1, body
    assert body["buckets"]["fresh"] == 1, body
    assert body["buckets"]["stale"] == 1, body
    assert len(body["servers"]) == 3, body

    response = c.get("/api/servers/freshness/fresh-server")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["server_id"] == "fresh-server", detail
    assert detail["state"] == "fresh", detail

    response = c.get("/api/servers/freshness/does-not-exist")
    assert response.status_code == 404, response.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)