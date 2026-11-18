"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds test data, hits the endpoint and validates the response.
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

    # ------------------------------------------------------------------ #
    # In‑memory SQLite engine and session factory
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed test data
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sess = TestingSession()
    # Job A – two completed runs
    sess.add(
        CadenceJobRun(
            id=1,
            job="job_a",
            status="SUCCESS",
            started_at=now - timedelta(seconds=20),
            finished_at=now - timedelta(seconds=10),
            rows_affected=100,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            id=2,
            job="job_a",
            status="SUCCESS",
            started_at=now - timedelta(seconds=10),
            finished_at=now - timedelta(seconds=5),
            rows_affected=150,
            detail={},
        )
    )
    # Job B – one completed run and one still running (finished_at NULL)
    sess.add(
        CadenceJobRun(
            id=3,
            job="job_b",
            status="SUCCESS",
            started_at=now - timedelta(seconds=30),
            finished_at=now - timedelta(seconds=20),
            rows_affected=200,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            id=4,
            job="job_b",
            status="RUNNING",
            started_at=now - timedelta(seconds=5),
            finished_at=None,
            rows_affected=50,
            detail={},
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override to use the in‑memory session
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Build FastAPI app with router and overrides
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute test request
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/cadence/job-duration")
    assert response.status_code == 200, response.text
    body = response.json()

    # ------------------------------------------------------------------ #
    # Validate response shape and content
    # ------------------------------------------------------------------ #
    jobs = body.get("jobs")
    assert isinstance(jobs, list), body
    assert len(jobs) == 2, body

    # Find job_a entry
    job_a = next((j for j in jobs if j.get("job") == "job_a"), None)
    assert job_a is not None, body
    assert job_a.get("runs") == 2, job_a
    median_a = job_a.get("median_seconds")
    assert isinstance(median_a, (int, float)), job_a
    # Expected median for job_a is (5 + 10) / 2 = 7.5 seconds
    assert abs(median_a - 7.5) < 1.0, f"median {median_a} not within 1.0 of 7.5"

    # Ensure job_b has only one completed run
    job_b = next((j for j in jobs if j.get("job") == "job_b"), None)
    assert job_b is not None, body
    assert job_b.get("runs") == 1, job_b

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)