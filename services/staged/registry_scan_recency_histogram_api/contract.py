"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.staged.registry_scan_recency_histogram_api.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity). A
non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip. That is the deliberate inverse of the 74% Tier-0 degradation
(FU-031) that let presence pass for correctness.

Run:  python -m services.staged.registry_scan_recency_histogram_api.contract   ->  prints PASS / FAIL, exits 0/1
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

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TestingSession()
    now = datetime.utcnow()
    s.add(McpServerRegistry(server_id="srv1", registry_source="src1", risk_tier="HIGH", last_scanned=None))
    s.add(McpServerRegistry(server_id="srv2", registry_source="src1", risk_tier="HIGH", last_scanned=now))
    s.add(McpServerRegistry(server_id="srv3", registry_source="src1", risk_tier="LOW", last_scanned=now - timedelta(days=40)))
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
    r = c.get("/api/registry/scan-recency?registry_source=src1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_servers"] == 3, body
    assert body["never_scanned"] == 1, body
    assert body["buckets"][0]["count"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)