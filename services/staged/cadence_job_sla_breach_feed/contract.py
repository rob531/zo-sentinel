"""Acceptance self-test for the cadence job SLA breach feed."""
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

    now = datetime.now().replace(microsecond=0)
    session = TestingSession()
    session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="scan",
                status="ok",
                started_at=now - timedelta(minutes=3),
                finished_at=now - timedelta(minutes=2),
                rows_affected=10,
                detail="ok",
            ),
            CadenceJobRun(
                id=2,
                job="scan",
                status="ok",
                started_at=now - timedelta(minutes=2),
                finished_at=now - timedelta(minutes=1),
                rows_affected=12,
                detail="ok",
            ),
            CadenceJobRun(
                id=3,
                job="scan",
                status="failed",
                started_at=now - timedelta(minutes=1),
                finished_at=now,
                rows_affected=0,
                detail="failed",
            ),
            CadenceJobRun(
                id=4,
                job="threat_intel",
                status="success",
                started_at=now - timedelta(seconds=30),
                finished_at=now - timedelta(seconds=20),
                rows_affected=5,
                detail="ok",
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

    with TestClient(app) as client:
        response = client.get("/api/cadence/sla-breaches?sla_seconds=60")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window_runs"] == 4, body
    assert len(body["jobs"]) == 2, body

    jobs = {entry["job"]: entry for entry in body["jobs"]}
    assert "scan" in jobs, body
    assert "threat_intel" in jobs, body

    scan = jobs["scan"]
    threat_intel = jobs["threat_intel"]
    assert scan["breached"] is True, body
    assert round(scan["success_rate"], 2) == 0.67, body
    assert threat_intel["breached"] is False, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)