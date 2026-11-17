"""Acceptance self-test for the cadence failure triage service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta, timezone

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
                id=1,
                job="seed-job",
                status="failed",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1) + timedelta(minutes=2),
                rows_affected=10,
                detail={"error": "seeded error message"},
            ),
            CadenceJobRun(
                id=2,
                job="seed-job",
                status="failed",
                started_at=now - timedelta(days=2),
                finished_at=now - timedelta(days=2) + timedelta(minutes=3),
                rows_affected=5,
                detail={"message": "another failure"},
            ),
            CadenceJobRun(
                id=3,
                job="healthy-job",
                status="success",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1) + timedelta(minutes=1),
                rows_affected=20,
                detail={},
            ),
            CadenceJobRun(
                id=4,
                job="running-job",
                status="running",
                started_at=now - timedelta(hours=1),
                finished_at=now,
                rows_affected=0,
                detail={},
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
    response = client.get("/api/cadence/failures?days=7")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["summary"]["failed_runs"] == 2, body
    assert body["summary"]["distinct_jobs"] == 1, body
    assert any(
        "seeded error message" in failure.get("error_hint", "")
        for failure in body["failures"]
    ), body
    assert all(
        failure.get("status", "").lower() not in {"success", "running"}
        for failure in body["failures"]
    ), body
    assert all(
        failure.get("job") != "healthy-job"
        and failure.get("job") != "running-job"
        for failure in body["failures"]
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