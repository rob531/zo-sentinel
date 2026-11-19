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


def run() -> bool:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, CadenceJobRun

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
    s.add_all(
        [
            CadenceJobRun(
                id=1,
                job="alpha",
                status="error",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1, minutes=59),
                rows_affected=10,
                detail={"message": "alpha failed"},
            ),
            CadenceJobRun(
                id=2,
                job="alpha",
                status="FAILED",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2, minutes=59),
                rows_affected=5,
                detail={"message": "alpha failed again"},
            ),
            CadenceJobRun(
                id=3,
                job="beta",
                status="error",
                started_at=now - timedelta(hours=4),
                finished_at=now - timedelta(hours=3, minutes=59),
                rows_affected=7,
                detail={"message": "beta failed"},
            ),
            CadenceJobRun(
                id=4,
                job="healthy-job",
                status="ok",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(minutes=59),
                rows_affected=20,
                detail={"message": "completed"},
            ),
            CadenceJobRun(
                id=5,
                job="old-job",
                status="error",
                started_at=now - timedelta(hours=48),
                finished_at=now - timedelta(hours=47),
                rows_affected=100,
                detail={"message": "outside the requested window"},
            ),
        ]
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
    r = c.get("/api/cadence/failures", params={"hours": 24})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_failures"] == 3, body
    assert len(body["jobs"]) == 2, body

    jobs = {entry["job"]: entry for entry in body["jobs"]}
    assert set(jobs) == {"alpha", "beta"}, body
    assert "healthy-job" not in jobs, body
    assert "old-job" not in jobs, body
    assert jobs["alpha"]["failures"] == 2, body
    assert jobs["beta"]["failures"] == 1, body
    assert all(
        str(entry.get("last_status", "")).lower() not in {"ok", "success"}
        for entry in body["jobs"]
    ), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)