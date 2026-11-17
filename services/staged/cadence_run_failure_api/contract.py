"""contract.py -- the service's ACCEPTANCE self‑test.

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
overrides the real DB dependency, seeds test data, hits the endpoint and
asserts the response shape and values.

Exit code 0 prints ``PASS``; any exception prints ``FAIL`` and exits 1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy utilities for an in‑memory SQLite DB
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer (must be used, not a stub)
    from app.db import get_session
    from app.models import Base, CadenceJobRun

    # Router that implements the endpoint under test
    from .router import router

    # ------------------------------------------------------------------ #
    # Build an in‑memory SQLite engine and create the schema
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed six CadenceJobRun rows across two jobs
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sess = TestingSession()
    rows = [
        # Job A
        CadenceJobRun(
            id=1,
            job="JobA",
            status="SUCCESS",
            started_at=now - timedelta(days=5, hours=2),
            finished_at=now - timedelta(days=5, hours=1, minutes=55),
            rows_affected=10,
            detail={},
        ),
        CadenceJobRun(
            id=2,
            job="JobA",
            status="FAILED",
            started_at=now - timedelta(days=4, hours=3),
            finished_at=now - timedelta(days=4, hours=2, minutes=50),
            rows_affected=0,
            detail={},
        ),
        CadenceJobRun(
            id=3,
            job="JobA",
            status="SUCCESS",
            started_at=now - timedelta(days=3, hours=1),
            finished_at=now - timedelta(days=3, minutes=45),
            rows_affected=5,
            detail={},
        ),
        # Job B
        CadenceJobRun(
            id=4,
            job="JobB",
            status="SUCCESS",
            started_at=now - timedelta(days=2, hours=4),
            finished_at=now - timedelta(days=2, hours=3, minutes=55),
            rows_affected=8,
            detail={},
        ),
        CadenceJobRun(
            id=5,
            job="JobB",
            status="FAILED",
            started_at=now - timedelta(days=1, hours=2),
            finished_at=now - timedelta(days=1, hours=1, minutes=58),
            rows_affected=0,
            detail={},
        ),
        # Job B with null finished_at (still running)
        CadenceJobRun(
            id=6,
            job="JobB",
            status="RUNNING",
            started_at=now - timedelta(hours=1),
            finished_at=None,
            rows_affected=3,
            detail={},
        ),
    ]
    sess.add_all(rows)
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override: provide a fresh session bound to the in‑memory DB
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Build FastAPI app, include router, apply override
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Exercise the endpoint
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/cadence/runs/failures")
    assert response.status_code == 200, f"status {response.status_code}: {response.text}"
    body = response.json()

    # ------------------------------------------------------------------ #
    # Assertions per the acceptance criteria
    # ------------------------------------------------------------------ #
    assert body["total_runs"] == 6, f"total_runs {body.get('total_runs')}"
    assert body["failed_runs"] == 2, f"failed_runs {body.get('failed_runs')}"
    assert isinstance(body["by_job"], list) and len(body["by_job"]) == 2, "by_job length"

    # Helper to locate a job entry
    def find_job(job_name: str):
        for entry in body["by_job"]:
            if entry.get("job") == job_name:
                return entry
        raise AssertionError(f"Job {job_name} not found in by_job")

    job_a = find_job("JobA")
    job_b = find_job("JobB")

    assert job_a["failures"] == 1, f"JobA failures {job_a.get('failures')}"
    assert job_b["failures"] == 1, f"JobB failures {job_b.get('failures')}"

    # Ensure duration fields are present and are numbers (null finished_at must not crash)
    for entry in (job_a, job_b):
        for key in ("median_duration_s", "p95_duration_s"):
            val = entry.get(key)
            assert isinstance(val, (int, float)), f"{entry['job']} {key} not numeric: {val}"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)