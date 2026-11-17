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
                id=1,
                job="alpha",
                status="succeeded",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                rows_affected=10,
                detail="completed",
            ),
            CadenceJobRun(
                id=2,
                job="alpha",
                status="failed",
                started_at=now - timedelta(minutes=3),
                finished_at=now - timedelta(minutes=2),
                rows_affected=0,
                detail="failed",
            ),
            CadenceJobRun(
                id=3,
                job="beta",
                status="succeeded",
                started_at=now - timedelta(minutes=2),
                finished_at=now - timedelta(minutes=1),
                rows_affected=5,
                detail="completed",
            ),
            CadenceJobRun(
                id=4,
                job="beta",
                status="succeeded",
                started_at=now - timedelta(minutes=1),
                finished_at=now,
                rows_affected=7,
                detail="completed",
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
    response = client.get("/api/cadence/runs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4, body
    assert body["by_status"].get("failed") == 1, body
    assert set(body["by_job"]) == {"alpha", "beta"}, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)