"""contract.py -- the service's ACCEPTANCE self‑test (the LIVENESS gate).

Runs the FastAPI service in an isolated subprocess with an in‑memory SQLite
session override and asserts that the `/api/cadence/backlog` endpoint returns
the expected payload.

The test seeds four `CadenceJobRun` rows across two distinct jobs:
* `good_job` – two recent successful runs.
* `bad_job`  – a single failed run that started more than the default
  `max_age_seconds` (3600) ago, making it stale while still being within the
  last 24 h window (so `failed_24h` == 1).

If all assertions pass the script prints ``PASS`` and exits with status 0.
"""
from __future__ import annotations

import sys
import datetime as _dt

def run() -> bool:
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy in‑memory engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data‑layer imports (must not be stubbed)
    from app.db import get_session
    from app.models import Base, CadenceJobRun

    # Service router
    from .router import router

    # ------------------------------------------------------------------ #
    # Set up an in‑memory SQLite DB and create the required tables.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed the database with deterministic test data.
    # ------------------------------------------------------------------ #
    now = _dt.datetime.utcnow()

    # Good job – two recent successful runs.
    good_run_1 = CadenceJobRun(
        job="good_job",
        status="SUCCESS",
        started_at=now - _dt.timedelta(minutes=10),
        finished_at=now - _dt.timedelta(minutes=9, seconds=30),
        rows_affected=10,
        detail="good run 1",
    )
    good_run_2 = CadenceJobRun(
        job="good_job",
        status="SUCCESS",
        started_at=now - _dt.timedelta(minutes=5),
        finished_at=now - _dt.timedelta(minutes=4, seconds=45),
        rows_affected=12,
        detail="good run 2",
    )

    # Bad job – a single failed run that is stale (started > 1 h ago) but
    # still within the last 24 h, so it counts as a failure.
    bad_run = CadenceJobRun(
        job="bad_job",
        status="FAIL",
        started_at=now - _dt.timedelta(hours=2),          # stale (>3600 s)
        finished_at=now - _dt.timedelta(hours=2) + _dt.timedelta(minutes=5),
        rows_affected=0,
        detail="bad run (stale failure)",
    )

    sess = TestingSession()
    sess.add_all([good_run_1, good_run_2, bad_run])
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
    # Build a minimal FastAPI app, inject the router, and apply the override.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute the request against the test client and validate the response.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/cadence/backlog")
    assert response.status_code == 200, response.text
    body = response.json()

    # Top‑level assertions
    assert body.get("total_jobs") == 2, body
    assert isinstance(body.get("jobs"), list), body

    # Locate the entry for the bad job
    bad_job_entry = next((j for j in body["jobs"] if j.get("job") == "bad_job"), None)
    assert bad_job_entry is not None, f"bad_job entry missing: {body}"
    assert bad_job_entry.get("failed_24h") == 1, bad_job_entry
    assert bad_job_entry.get("stale") is True, bad_job_entry

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)