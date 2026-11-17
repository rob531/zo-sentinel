"""HTTP surface for cadence run history."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_cadence_run_history

router = APIRouter(prefix="/api", tags=["cadence_run_history"])


class JobRunSummary(BaseModel):
    job: str
    last_status: str
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_rows_affected: int | None
    run_count: int
    failure_count: int


class CadenceRun(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    rows_affected: int | None
    detail: str | None


class CadenceRunHistoryResponse(BaseModel):
    jobs: list[JobRunSummary]
    runs: list[CadenceRun]


@router.get("/cadence/runs", response_model=CadenceRunHistoryResponse)
def get_cadence_runs(
    db: Session = Depends(get_session),
    job: str | None = None,
    limit: int = Query(default=50),
) -> CadenceRunHistoryResponse:
    bounded_limit = max(1, min(200, limit))
    history = get_cadence_run_history(db, job=job, limit=bounded_limit)
    return CadenceRunHistoryResponse(
        jobs=history["jobs"],
        runs=history["runs"],
    )


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    base_time = datetime(2025, 1, 1, 12, 0, 0)
    seed_runs = [
        CadenceJobRun(
            id=1,
            job="alpha",
            status="failed",
            started_at=base_time,
            finished_at=base_time + timedelta(seconds=5),
            rows_affected=0,
            detail="transient failure",
        ),
        CadenceJobRun(
            id=2,
            job="alpha",
            status="success",
            started_at=base_time + timedelta(minutes=1),
            finished_at=base_time + timedelta(minutes=1, seconds=5),
            rows_affected=10,
            detail="completed",
        ),
        CadenceJobRun(
            id=3,
            job="beta",
            status="success",
            started_at=base_time + timedelta(minutes=2),
            finished_at=base_time + timedelta(minutes=2, seconds=5),
            rows_affected=20,
            detail="completed",
        ),
        CadenceJobRun(
            id=4,
            job="gamma",
            status="success",
            started_at=base_time + timedelta(minutes=3),
            finished_at=base_time + timedelta(minutes=3, seconds=5),
            rows_affected=30,
            detail="completed",
        ),
        CadenceJobRun(
            id=5,
            job="beta",
            status="success",
            started_at=base_time + timedelta(minutes=4),
            finished_at=base_time + timedelta(minutes=4, seconds=5),
            rows_affected=40,
            detail="completed",
        ),
    ]

    with TestSession() as db:
        db.add_all(seed_runs)
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/cadence/runs")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 3
    assert sum(job["failure_count"] for job in payload["jobs"]) == 1

    limited_response = client.get("/api/cadence/runs", params={"limit": 1})
    assert limited_response.status_code == 200
    assert len(limited_response.json()["runs"]) == 1

    print("PASS")