"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
If the endpoint returns the expected data the script prints ``PASS`` and
exits with status 0, otherwise it prints ``FAIL`` and exits with status 1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    # FastAPI test harness
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real app data layer
    from app.db import get_session
    from app.models import Base, CadenceJobRun

    # Service router
    from .router import router

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite DB that mimics the real schema
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed three rows: two failures and one success for the same job
    now = datetime.utcnow()
    sess = TestingSession()
    sess.add(
        CadenceJobRun(
            job="myjob",
            status="failed",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=2, minutes=50),
            rows_affected=10,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            job="myjob",
            status="success",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=55),
            rows_affected=20,
            detail={},
        )
    )
    sess.add(
        CadenceJobRun(
            job="myjob",
            status="failed",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=55),
            rows_affected=30,
            detail={},
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------
    # Dependency override so the service uses the in‑memory DB
    # ------------------------------------------------------------------
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------
    # Execute the request against the test client
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/cadence/jobs/failures")
    assert response.status_code == 200, response.text
    body = response.json()

    # ------------------------------------------------------------------
    # Validate the payload
    # ------------------------------------------------------------------
    jobs = body.get("jobs", [])
    assert len(jobs) == 1, body
    job_info = jobs[0]
    assert job_info["job"] == "myjob", job_info
    assert job_info["total"] == 3, job_info
    assert job_info["failed"] == 2, job_info

    # failure_rate should be 2/3 ≈ 0.6667
    expected_rate = 2 / 3
    actual_rate = job_info["failure_rate"]
    assert abs(actual_rate - expected_rate) < 0.01, f"{actual_rate} != {expected_rate}"

    # rows_affected should be the sum of the three rows
    assert job_info["rows_affected"] == 60, job_info

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)