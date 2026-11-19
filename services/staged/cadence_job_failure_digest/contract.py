"""Acceptance self-test for the cadence job failure digest service."""
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
                job="healthy-job",
                status="success",
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                rows_affected=100,
                detail=None,
            ),
            CadenceJobRun(
                job="healthy-job",
                status="success",
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=19),
                rows_affected=200,
                detail=None,
            ),
            CadenceJobRun(
                job="failing-job",
                status="failed",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                rows_affected=0,
                detail={"error": "boom"},
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
    response = client.get("/api/cadence/failure-digest")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["jobs"]) == 2, body

    failing = next(job for job in body["jobs"] if job["job"] == "failing-job")
    assert failing["failures"] == 1, body
    assert failing["last_error"] == "boom", body
    assert body["worst_jobs"][0] == "failing-job", body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)