from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun
from .logic import get_cadence_run_duration

router = APIRouter(prefix="/api", tags=["cadence"])


@router.get("/cadence/runs")
def cadence_runs(
    job: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 30,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    return get_cadence_run_duration(
        session,
        job=job,
        status=status,
        days=days,
        limit=limit,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    from app.db import Base

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        CadenceJobRun(
            job="alpha",
            status="success",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            rows_affected=10,
            detail="alpha completed",
        ),
        CadenceJobRun(
            job="alpha",
            status="success",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            rows_affected=5,
            detail="alpha completed",
        ),
        CadenceJobRun(
            job="alpha",
            status="success",
            started_at=now - timedelta(minutes=30),
            finished_at=now - timedelta(minutes=29),
            rows_affected=7,
            detail="alpha completed",
        ),
        CadenceJobRun(
            job="beta",
            status="success",
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=14),
            rows_affected=3,
            detail="beta completed",
        ),
        CadenceJobRun(
            job="beta",
            status="failed",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            rows_affected=0,
            detail="beta failed",
        ),
    ]

    db = TestingSessionLocal()
    try:
        db.add_all(rows)
        db.commit()
    finally:
        db.close()

    response = TestClient(test_app).get("/api/cadence/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert "alpha" in payload["by_job"]
    assert "beta" in payload["by_job"]
    assert payload["by_job"]["beta"]["failed"] == 1
    assert payload["by_job"]["beta"]["median_duration_seconds"] is not None
    print("PASS")