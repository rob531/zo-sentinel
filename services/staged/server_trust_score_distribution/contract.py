"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.<stage>.<name>.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity).

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not
a silent skip.

Run:  python -m services.staged.server_trust_score_distribution.contract
"""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TestingSession()
    s.add(McpServerRegistry(server_id="srv1", name="A", trust_score=10))
    s.add(McpServerRegistry(server_id="srv2", name="B", trust_score=50))
    s.add(McpServerRegistry(server_id="srv3", name="C", trust_score=85))
    s.add(McpServerRegistry(server_id="srv4", name="D", trust_score=None))
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
    r = c.get("/api/registry/trust-score-distribution")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_servers"] == 4, body
    assert body["scored_servers"] == 3, body
    assert body["unscored_servers"] == 1, body
    assert sum(bucket["count"] for bucket in body["buckets"]) == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)