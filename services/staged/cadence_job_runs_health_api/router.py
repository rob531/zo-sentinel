"""Thin HTTP surface for cadence job-run health."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import HealthResponse, get_cadence_job_runs_health

router = APIRouter(prefix="/api", tags=["cadence_job_runs_health_api"])


@router.get("/cadence/health", response_model=HealthResponse)
def get_cadence_health(session: Session = Depends(get_session)) -> HealthResponse:
    return get_cadence_job_runs_health(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import CadenceJobRun

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    seed_runs = [
        CadenceJobRun(
            job="job_success_a",
            status="success",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=55),
            rows_affected=12,
            detail={},
        ),
        CadenceJobRun(
            job="job_failed",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=50),
            rows_affected=3,
            detail={"error": {"message": "test failure"}},
        ),
        CadenceJobRun(
            job="job_running",
            status="running",
            started_at=now - timedelta(minutes=10),
            finished_at=None,
            rows_affected=None,
            detail={},
        ),
        CadenceJobRun(
            job="job_error",
            status="error",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=2, minutes=55),
            rows_affected=0,
            detail={"error": {"message": "test error"}},
        ),
        CadenceJobRun(
            job="job_success_b",
            status="success",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3, minutes=50),
            rows_affected=7,
            detail={},
        ),
    ]

    with TestSessionLocal() as session:
        session.add_all(seed_runs)
        session.commit()

    def override_get_session():
        with TestSessionLocal() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/cadence/health")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    payload = response.json()
    assert payload["overall_status"] in {"healthy", "degraded", "stale"}
    assert len(payload["jobs"]) == 5, f"Expected 5 jobs, got {len(payload['jobs'])}"
    assert payload["summary"] == {
        "total": 5,
        "success": 2,
        "failed": 1,
        "running": 1,
        "error": 1,
    }
    assert all(
        {"name", "status", "last_run_at", "duration_seconds", "rows_affected", "error_detail"}
        <= job.keys()
        for job in payload["jobs"]
    )
    engine.dispose()
    print("PASS")