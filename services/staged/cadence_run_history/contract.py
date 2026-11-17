"""Acceptance self-test for the cadence_run_history service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import CadenceJobRun

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="alpha",
                status="success",
                started_at=datetime(2026, 1, 1, 10, 0, 0),
                finished_at=datetime(2026, 1, 1, 10, 1, 0),
                rows_affected=10,
                detail="completed",
            ),
            CadenceJobRun(
                id=2,
                job="alpha",
                status="failed",
                started_at=datetime(2026, 1, 2, 10, 0, 0),
                finished_at=datetime(2026, 1, 2, 10, 1, 0),
                rows_affected=0,
                detail="failed validation",
            ),
            CadenceJobRun(
                id=3,
                job="beta",
                status="success",
                started_at=datetime(2026, 1, 3, 10, 0, 0),
                finished_at=datetime(2026, 1, 3, 10, 2, 0),
                rows_affected=25,
                detail="completed",
            ),
            CadenceJobRun(
                id=4,
                job="gamma",
                status="success",
                started_at=datetime(2026, 1, 4, 10, 0, 0),
                finished_at=datetime(2026, 1, 4, 10, 1, 0),
                rows_affected=5,
                detail="completed",
            ),
            CadenceJobRun(
                id=5,
                job="gamma",
                status="success",
                started_at=datetime(2026, 1, 5, 10, 0, 0),
                finished_at=datetime(2026, 1, 5, 10, 1, 0),
                rows_affected=7,
                detail="completed",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/cadence/runs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["jobs"]) == 3, body

    failing_jobs = [job for job in body["jobs"] if job["failure_count"] == 1]
    assert len(failing_jobs) == 1, body
    assert failing_jobs[0]["job"] == "alpha", body

    limited = client.get("/api/cadence/runs", params={"limit": 1})
    assert limited.status_code == 200, limited.text
    assert len(limited.json()["runs"]) == 1, limited.json()

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)