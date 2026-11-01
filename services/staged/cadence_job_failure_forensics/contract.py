from __future__ import annotations

import sys
from datetime import datetime, timedelta


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

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            CadenceJobRun(
                id=101,
                job="nightly-import",
                status="success",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1) + timedelta(minutes=2),
                rows_affected=10,
                detail="completed",
            ),
            CadenceJobRun(
                id=102,
                job="nightly-import",
                status="failed",
                started_at=now - timedelta(days=2),
                finished_at=now - timedelta(days=2) + timedelta(minutes=1),
                rows_affected=0,
                detail="upstream timeout",
            ),
            CadenceJobRun(
                id=103,
                job="nightly-import",
                status="running",
                started_at=now - timedelta(hours=1),
                finished_at=None,
                rows_affected=None,
                detail="in progress",
            ),
            CadenceJobRun(
                id=104,
                job="hourly-sync",
                status="success",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1) + timedelta(minutes=1),
                rows_affected=20,
                detail="completed",
            ),
            CadenceJobRun(
                id=105,
                job="hourly-sync",
                status="failed",
                started_at=now - timedelta(days=3),
                finished_at=now - timedelta(days=3) + timedelta(minutes=1),
                rows_affected=0,
                detail="database unavailable",
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
    response = client.get("/api/cadence/jobs/nightly-import/failures?days=30")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["job"] == "nightly-import", body
    assert body["days"] == 30, body
    assert body["failure_count"] == 1, body
    assert len(body["runs"]) == 3, body
    assert all(run["status"] for run in body["runs"]), body
    assert {run["id"] for run in body["runs"]} == {101, 102, 103}, body
    assert all(run["status"] != "failed" or run["id"] == 102 for run in body["runs"]), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)