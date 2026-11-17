"""Acceptance self-test for the cadence run failure probe."""
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

    now = datetime.now(timezone.utc)
    session = TestingSession()
    session.add_all(
        [
            CadenceJobRun(
                job="failing-job",
                status="failed",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(hours=1) + timedelta(seconds=10),
                rows_affected=3,
                detail="seeded failure",
            ),
            CadenceJobRun(
                job="failing-job",
                status="failed",
                started_at=now - timedelta(days=1, hours=2),
                finished_at=now - timedelta(days=1, hours=2) + timedelta(seconds=20),
                rows_affected=4,
                detail="seeded failure",
            ),
            CadenceJobRun(
                job="healthy-job",
                status="success",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2) + timedelta(seconds=5),
                rows_affected=1,
                detail="seeded success",
            ),
            CadenceJobRun(
                job="healthy-job",
                status="success",
                started_at=now - timedelta(days=1, hours=3),
                finished_at=now - timedelta(days=1, hours=3) + timedelta(seconds=15),
                rows_affected=2,
                detail="seeded success",
            ),
            CadenceJobRun(
                job="mixed-job",
                status="running",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=3) + timedelta(seconds=7),
                rows_affected=0,
                detail="seeded other status",
            ),
            CadenceJobRun(
                job="mixed-job",
                status="success",
                started_at=now - timedelta(days=1, hours=4),
                finished_at=now - timedelta(days=1, hours=4) + timedelta(seconds=9),
                rows_affected=1,
                detail="seeded success",
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

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = override_get_session

    client = TestClient(application)
    response = client.get("/api/cadence/run-failures", params={"days": 2})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 2, body
    assert body["totals"]["runs"] == 6, body
    assert body["totals"]["failed"] == 2, body
    assert body["totals"]["worst_job"] == "failing-job", body

    jobs = {entry["job"]: entry for entry in body["jobs"]}
    assert "failing-job" in jobs, body
    assert jobs["failing-job"]["failure_rate"] == 1.0, body
    assert abs(jobs["failing-job"]["avg_seconds"] - 15.0) <= 1.0, body
    assert jobs["failing-job"]["max_seconds"] == 20.0, body
    assert jobs["failing-job"]["rows_affected"] == 7, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)