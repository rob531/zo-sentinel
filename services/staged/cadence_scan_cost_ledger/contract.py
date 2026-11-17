"""Acceptance self-test for the cadence scan cost ledger service."""
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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            CadenceJobRun(
                job="catalog_scan",
                status="success",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=3) + timedelta(seconds=30),
                rows_affected=10,
                detail="completed",
            ),
            CadenceJobRun(
                job="catalog_scan",
                status="success",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2) + timedelta(seconds=50),
                rows_affected=15,
                detail="completed",
            ),
            CadenceJobRun(
                job="dependency_scan",
                status="failed",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(hours=1) + timedelta(seconds=20),
                rows_affected=0,
                detail="failed",
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
    response = client.get("/api/cadence/cost?days=30")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == 30, body
    assert len(body["jobs"]) == 2, body

    jobs = {item["job"]: item for item in body["jobs"]}
    assert jobs["dependency_scan"]["failures"] == 1, body
    assert jobs["dependency_scan"]["median_seconds"] >= 0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)