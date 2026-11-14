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

Run:  python -m services.staged.cadence_job_failure_detail.contract
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
    from app.models import Base, CadenceJobRun

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    recent = now - timedelta(hours=1)
    old = now - timedelta(days=10)

    session = testing_session()
    try:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="alpha",
                    status="failed",
                    started_at=recent,
                    finished_at=recent + timedelta(minutes=1),
                    rows_affected=3,
                    detail="recent alpha failure",
                ),
                CadenceJobRun(
                    id=2,
                    job="alpha",
                    status="success",
                    started_at=recent,
                    finished_at=recent + timedelta(minutes=1),
                    rows_affected=10,
                    detail="recent alpha success",
                ),
                CadenceJobRun(
                    id=3,
                    job="beta",
                    status="failed",
                    started_at=recent,
                    finished_at=recent + timedelta(minutes=2),
                    rows_affected=0,
                    detail="recent beta failure",
                ),
                CadenceJobRun(
                    id=4,
                    job="beta",
                    status="success",
                    started_at=old,
                    finished_at=old + timedelta(minutes=1),
                    rows_affected=5,
                    detail="old beta success",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def _override():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/cadence/jobs/failures", params={"days": 1, "limit": 50})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 1, body
    assert body["limit"] == 50, body
    assert len(body["failures"]) == 2, body
    assert all(row["status"] != "success" for row in body["failures"]), body
    assert all(row["id"] != 4 for row in body["failures"]), body
    assert any(row["failures"] == 1 for row in body["by_job"]), body
    assert {row["job"] for row in body["failures"]} == {"alpha", "beta"}, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)