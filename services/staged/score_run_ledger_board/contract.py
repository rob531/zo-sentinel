from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, CadenceJobRun, FreshnessStat

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    job_name = "score-ledger"
    failing_job_name = "failing-score-ledger"
    seeded_compute_seconds = 1.25
    started_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    finished_at = datetime(2025, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

    session = TestingSession()
    try:
        session.add_all(
            [
                CadenceJobRun(
                    job=job_name,
                    status="ok",
                    started_at=started_at,
                    finished_at=finished_at,
                    rows_affected=10,
                    detail="first successful run",
                ),
                CadenceJobRun(
                    job=job_name,
                    status="ok",
                    started_at=finished_at,
                    finished_at=finished_at,
                    rows_affected=20,
                    detail="second successful run",
                ),
                CadenceJobRun(
                    job=failing_job_name,
                    status="failed",
                    started_at=finished_at,
                    finished_at=finished_at,
                    rows_affected=0,
                    detail="failed run",
                ),
                FreshnessStat(
                    surface=job_name,
                    payload={"status": "ok"},
                    compute_seconds=seeded_compute_seconds,
                ),
            ]
        )
        session.commit()
    finally:
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

    with TestClient(app) as client:
        response = client.get("/api/scores/runs/ledger")
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["totals"]["runs"] == 3, body
        assert body["totals"]["failed"] == 1, body
        assert any(job["job"] == failing_job_name for job in body["jobs"]), body
        assert body["surfaces"][0]["compute_seconds_avg"] == seeded_compute_seconds, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)