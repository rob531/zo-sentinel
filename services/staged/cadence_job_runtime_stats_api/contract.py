"""Acceptance self-test for the cadence job runtime stats API."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta

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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = testing_session()
    try:
        run_id = 0
        for job, failed, incomplete in (
            ("alpha", False, False),
            ("beta", True, False),
            ("gamma", False, True),
        ):
            for index in range(4):
                run_id += 1
                started_at = now - timedelta(hours=run_id)
                is_failed = failed and index == 3
                is_incomplete = incomplete and index == 3
                session.add(
                    CadenceJobRun(
                        job=job,
                        status="failed" if is_failed else "running" if is_incomplete else "success",
                        started_at=started_at,
                        finished_at=None if is_incomplete else started_at + timedelta(seconds=10),
                        rows_affected=10,
                        detail="acceptance fixture",
                    )
                )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/cadence/jobs/runtime-stats", params={"days": 7})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 7, body
    assert len(body["jobs"]) == 3, body

    jobs = {entry["job"]: entry for entry in body["jobs"]}
    assert jobs["beta"]["runs"] == 4, body
    assert jobs["beta"]["failed_runs"] == 1, body
    assert abs(jobs["beta"]["failure_rate"] - 0.25) < 1e-9, body
    assert jobs["gamma"]["incomplete_runs"] >= 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)