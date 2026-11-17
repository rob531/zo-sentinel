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
                job="alpha",
                status="ok",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1),
                rows_affected=10,
                detail="alpha ok",
            ),
            CadenceJobRun(
                job="alpha",
                status="error",
                started_at=now - timedelta(days=2),
                finished_at=now - timedelta(days=2),
                rows_affected=20,
                detail="alpha error",
            ),
            CadenceJobRun(
                job="beta",
                status="ok",
                started_at=now - timedelta(days=3),
                finished_at=now - timedelta(days=3),
                rows_affected=30,
                detail="beta ok",
            ),
            CadenceJobRun(
                job="beta",
                status="ok",
                started_at=now - timedelta(days=4),
                finished_at=now - timedelta(days=4),
                rows_affected=40,
                detail="beta ok",
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
    response = client.get("/api/cadence/rows-processed-leaderboard?days=7")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 7, body
    assert body["grand_total_rows_affected"] == 100, body

    jobs = {entry["job"]: entry for entry in body["jobs"]}
    assert "alpha" in jobs, body
    assert "beta" in jobs, body
    assert jobs["alpha"]["total_rows_affected"] == 30, body
    assert jobs["beta"]["total_rows_affected"] == 70, body
    assert jobs["alpha"]["success_runs"] == 1, body
    assert jobs["alpha"]["failed_runs"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)