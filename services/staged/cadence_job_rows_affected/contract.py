"""Acceptance self-test for the cadence_job_rows_affected service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = TestingSession()
    session.add_all(
        [
            CadenceJobRun(
                job="daily-success",
                status="success",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1) + timedelta(seconds=3),
                rows_affected=10,
                detail="first",
            ),
            CadenceJobRun(
                job="daily-success",
                status="success",
                started_at=now - timedelta(days=2),
                finished_at=now - timedelta(days=2) + timedelta(seconds=5),
                rows_affected=20,
                detail="second",
            ),
            CadenceJobRun(
                job="nightly-failure",
                status="failed",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1) + timedelta(seconds=2),
                rows_affected=5,
                detail="failure",
            ),
        ]
    )
    session.commit()
    session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/cadence/jobs/rows-affected?days=7&limit=50")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 7, body
    assert isinstance(body["jobs"], list), body
    assert len(body["jobs"]) == 2, body

    jobs = {item["job"]: item for item in body["jobs"]}
    success_job = jobs["daily-success"]
    failure_job = jobs["nightly-failure"]

    assert success_job["run_count"] == 2, body
    assert success_job["success_count"] == 2, body
    assert success_job["failure_count"] == 0, body
    assert success_job["total_rows_affected"] == 30, body
    assert failure_job["failure_count"] == 1, body
    assert failure_job["total_rows_affected"] == 5, body

    assert body["jobs"] == sorted(
        body["jobs"],
        key=lambda item: (-item["total_rows_affected"], item["job"]),
    ), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)