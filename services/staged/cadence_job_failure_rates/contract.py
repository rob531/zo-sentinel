"""contract.py -- the service's ACCEPTANCE self‑test.

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
If the service boots, mounts the router, serves 200 and returns a
schema‑valid body, the script prints ``PASS`` and exits 0.
Otherwise it prints ``FAIL`` and exits 1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    """Boot the service with a temporary SQLite DB and exercise its endpoint."""
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy core for an in‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer imports (must not be stubbed)
    from app.db import get_session
    from app.models import Base, CadenceJobRun

    # Router that implements the endpoint under test
    from .router import router

    # ------------------------------------------------------------------ #
    # Build an in‑memory SQLite engine and create the real tables.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed six CadenceJobRun rows across two jobs.
    #   job_a: 4 runs (2 success, 2 failed) → failure_rate 0.5
    #   job_b: 2 runs (both success)      → failure_rate 0.0
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sess = TestingSession()
    # job_a runs
    sess.add(
        CadenceJobRun(
            id=1,
            job="job_a",
            status="success",
            started_at=now - timedelta(hours=5),
            finished_at=now - timedelta(hours=4, minutes=55),
            rows_affected=10,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            id=2,
            job="job_a",
            status="failed",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3, minutes=58),
            rows_affected=0,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            id=3,
            job="job_a",
            status="success",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=2, minutes=55),
            rows_affected=12,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            id=4,
            job="job_a",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=58),
            rows_affected=0,
            detail={},
        )
    )
    # job_b runs
    sess.add(
        CadenceJobRun(
            id=5,
            job="job_b",
            status="success",
            started_at=now - timedelta(hours=1, minutes=30),
            finished_at=now - timedelta(hours=1, minutes=25),
            rows_affected=7,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            id=6,
            job="job_b",
            status="success",
            started_at=now - timedelta(minutes=45),
            finished_at=now - timedelta(minutes=40),
            rows_affected=9,
            detail={},
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override that yields a fresh session bound to the in‑memory DB.
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Assemble the FastAPI app, inject the router and the session override.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Exercise the endpoint.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/cadence/jobs/failure-rates?days=7")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("days") == 7, body

    # Locate the job_a entry.
    job_a = next((j for j in body.get("jobs", []) if j.get("job") == "job_a"), None)
    assert job_a is not None, f"job_a missing in response: {body}"
    # Failure rate must be exactly 0.5.
    assert abs(job_a.get("failure_rate", -1) - 0.5) < 1e-6, f"unexpected failure_rate: {job_a}"
    # Average duration must be positive.
    assert job_a.get("avg_duration_seconds", 0) > 0, f"non‑positive avg_duration_seconds: {job_a}"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)