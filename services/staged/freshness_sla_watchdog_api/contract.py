"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.<stage>.<name>.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity). A
non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip. That is the deliberate inverse of the 74% Tier-0 degradation
(FU-031) that let presence pass for correctness.

Run:  python -m services.staged.freshness_sla_watchdog_api.contract
        ->  prints PASS / FAIL, exits 0/1
"""
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
    from app.models import Base, FreshnessStat, McpServerRegistry

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    s = TestingSession()
    s.add(
        FreshnessStat(
            surface="stale-surface",
            payload=json.dumps({"max_age_seconds": 60}),
            computed_at=now - timedelta(hours=2),
            compute_seconds=30,
        )
    )
    s.add(
        FreshnessStat(
            surface="fresh-surface",
            payload=json.dumps({"max_age_seconds": 3600}),
            computed_at=now,
            compute_seconds=30,
        )
    )
    s.add(
        FreshnessStat(
            surface="empty-payload-surface",
            payload=json.dumps({}),
            computed_at=now,
            compute_seconds=30,
        )
    )
    s.add(McpServerRegistry(server_id="srv-1", last_assessed=None))
    s.add(McpServerRegistry(server_id="srv-2", last_assessed=now))
    s.add(McpServerRegistry(server_id="srv-3", last_assessed=now))
    s.add(McpServerRegistry(server_id="srv-4", last_assessed=now))
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
    r = c.get("/api/freshness/sla")
    assert r.status_code == 200, r.text
    body = r.json()

    surfaces = body["surfaces"]
    summary = body["summary"]
    assert len(surfaces) == 3, body
    assert sum(1 for row in surfaces if row["status"] == "STALE") == 1, body
    assert summary["stale_surfaces"] == 1, body
    assert summary["never_assessed_servers"] == 1, body
    assert summary["total_servers"] == 4, body
    assert "generated_at" in body, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)