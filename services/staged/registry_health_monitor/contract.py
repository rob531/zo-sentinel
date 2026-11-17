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

Run:  python -m services._exemplar.contract   ->  prints PASS / FAIL, exits 0/1
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
    old_seen = now - timedelta(days=45)
    recent_seen = now - timedelta(days=2)

    s = TestingSession()
    s.add(
        McpServerRegistry(
            server_id="registry-contract-1",
            name="Registry One",
            registry_source="alpha",
            first_seen=old_seen,
            last_seen=old_seen,
            last_scanned=old_seen,
            scan_count=3,
            risk_tier="HIGH",
            verdict="malicious",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="registry-contract-2",
            name="Registry Two",
            registry_source="alpha",
            first_seen=recent_seen,
            last_seen=recent_seen,
            last_scanned=recent_seen,
            scan_count=1,
            risk_tier="LOW",
            verdict="clean",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="registry-contract-3",
            name="Registry Three",
            registry_source="beta",
            first_seen=recent_seen,
            last_seen=recent_seen,
            last_scanned=recent_seen,
            scan_count=2,
            risk_tier="MEDIUM",
            verdict="suspicious",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="registry-contract-4",
            name="Registry Four",
            registry_source="beta",
            first_seen=recent_seen,
            last_seen=recent_seen,
            last_scanned=None,
            scan_count=0,
            risk_tier="LOW",
            verdict="unknown",
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
    r = c.get("/api/registry/health", params={"days": 30})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total"] == 4, body
    assert sum(item["never_scanned"] for item in body["by_source"]) == 1, body
    assert {item["registry_source"] for item in body["by_source"]} == {
        "alpha",
        "beta",
    }, body
    assert body["by_risk_tier"] == {"HIGH": 1, "LOW": 2, "MEDIUM": 1}, body
    assert len(body["recently_seen"]) == 4, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)