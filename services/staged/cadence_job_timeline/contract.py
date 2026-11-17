"""Acceptance self-test for the cadence_job_timeline service."""
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
    from app.models import Base, CadenceJobRun

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="scanner",
                status="success",
                started_at=datetime(2024, 1, 1, 12, 0, 0),
                finished_at=datetime(2024, 1, 1, 12, 0, 10),
                rows_affected=5,
                detail={"message": "completed"},
            ),
            CadenceJobRun(
                id=2,
                job="scanner",
                status="failure",
                started_at=datetime(2024, 1, 1, 12, 1, 0),
                finished_at=datetime(2024, 1, 1, 12, 1, 20),
                rows_affected=2,
                detail={"message": "failed"},
            ),
            CadenceJobRun(
                id=3,
                job="enricher",
                status="success",
                started_at=datetime(2024, 1, 1, 12, 2, 0),
                finished_at=datetime(2024, 1, 1, 12, 2, 15),
                rows_affected=3,
                detail={"message": "completed"},
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
    response = client.get("/api/cadence/jobs/timeline")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["jobs"]) == 2, body
    assert body["total_runs"] == 3, body

    scanner = next(job for job in body["jobs"] if job["job"] == "scanner")
    assert scanner["success_count"] == 1, body
    assert scanner["failure_count"] == 1, body

    completed_runs = [
        run for run in body["runs"]
        if run["job"] == "scanner" and run["status"] == "success"
    ]
    assert completed_runs, body
    assert completed_runs[0]["duration_seconds"] > 0, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)