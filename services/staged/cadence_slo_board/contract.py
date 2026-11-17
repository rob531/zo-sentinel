"""contract.py -- acceptance self‑test for the cadence_slo_board service.

Running this module as ``python -m services.staged.cadence_slo_board.contract``
starts a FastAPI app with an in‑memory SQLite database, seeds it with a small
fixture, exercises the public ``GET /api/cadence/slo`` endpoint and asserts
that the response matches the contract.  On success it prints ``PASS`` and
exits with status 0; any exception is printed as ``FAIL`` and exits with 1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    """Boot the service with a test DB, hit the endpoint and validate the payload."""
    # FastAPI & test utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy test engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data‑layer imports (must not be stubbed)
    from app.db import get_session
    from app.models import Base, CadenceJobRun

    # Router that implements the public API
    from .router import router

    # ------------------------------------------------------------------ #
    # 1️⃣  Build an in‑memory SQLite engine and create the schema.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # 2️⃣  Seed fixture data:
    #     * job "alpha" – two successful runs (status "ok")
    #     * job "beta"  – one failed run (status "failed")
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sess = TestingSession()
    sess.add_all(
        [
            CadenceJobRun(
                id=1,
                job="alpha",
                status="ok",
                started_at=now - timedelta(hours=2, minutes=5),
                finished_at=now - timedelta(hours=2, minutes=4, seconds=30),
                rows_affected=10,
                detail="first success",
            ),
            CadenceJobRun(
                id=2,
                job="alpha",
                status="ok",
                started_at=now - timedelta(hours=1, minutes=15),
                finished_at=now - timedelta(hours=1, minutes=14, seconds=45),
                rows_affected=20,
                detail="second success",
            ),
            CadenceJobRun(
                id=3,
                job="beta",
                status="failed",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2, minutes=59, seconds=30),
                rows_affected=5,
                detail="run failed",
            ),
        ]
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # 3️⃣  Dependency override so the FastAPI app uses the test session.
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # 4️⃣  Exercise the endpoint and assert the contract.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/cadence/slo")  # default hours = 24
    assert response.status_code == 200, response.text
    payload = response.json()

    # top‑level fields
    assert isinstance(payload.get("hours"), int), payload
    assert payload["hours"] == 24, payload

    jobs = payload.get("jobs")
    assert isinstance(jobs, list), payload
    assert len(jobs) == 2, payload

    # because the router sorts by failures descending then job name,
    # the failing job ("beta") should be first.
    failing = jobs[0]
    assert failing["job"] == "beta", failing
    assert failing["failures"] == 1, failing
    assert failing["runs"] == 1, failing
    assert failing["success_rate"] == 0.0, failing
    assert failing["rows_affected"] == 5, failing
    assert isinstance(failing["mean_duration_seconds"], float), failing

    # the successful job ("alpha")
    success = jobs[1]
    assert success["job"] == "alpha", success
    assert success["failures"] == 0, success
    assert success["runs"] == 2, success
    assert success["success_rate"] == 1.0, success
    assert success["rows_affected"] == 30, success
    assert isinstance(success["mean_duration_seconds"], float), success

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – fail loudly
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)