"""HTTP surface for cadence job failure reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_job_failure_breakdown as _get_job_failure_breakdown
except ImportError:
    try:
        from .logic import aggregate_job_failures as _get_job_failure_breakdown
    except ImportError:
        try:
            from .logic import job_failure_breakdown as _get_job_failure_breakdown
        except ImportError:
            from .logic import get_failure_breakdown as _get_job_failure_breakdown

router = APIRouter(prefix="/api", tags=["cadence-job-failure-breakdown"])


class JobFailureBreakdown(BaseModel):
    job: str
    total: int
    failed: int
    failure_rate: float
    last_failure_at: datetime | None
    rows_affected: int


class CadenceJobFailureResponse(BaseModel):
    days: int
    jobs: list[JobFailureBreakdown]


@router.get(
    "/cadence/jobs/failures",
    response_model=CadenceJobFailureResponse,
)
def get_cadence_job_failures(
    days: int = Query(30, ge=1),
    db: Session = Depends(get_session),
) -> CadenceJobFailureResponse:
    result: Any = _get_job_failure_breakdown(db, days)
    jobs = result.get("jobs", []) if isinstance(result, dict) else result
    return CadenceJobFailureResponse(days=days, jobs=jobs)


def _self_test() -> None:
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import CadenceJobRun

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.utcnow()
    with session_factory() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="sync-users",
                    status="failed",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=0,
                    detail={"error": "timeout"},
                ),
                CadenceJobRun(
                    id=2,
                    job="sync-users",
                    status="success",
                    started_at=now - timedelta(minutes=4),
                    finished_at=now - timedelta(minutes=3),
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    id=3,
                    job="sync-users",
                    status="failed",
                    started_at=now - timedelta(minutes=3),
                    finished_at=now - timedelta(minutes=2),
                    rows_affected=0,
                    detail={"error": "constraint"},
                ),
                CadenceJobRun(
                    id=4,
                    job="sync-reports",
                    status="success",
                    started_at=now - timedelta(minutes=2),
                    finished_at=now - timedelta(minutes=1),
                    rows_affected=25,
                    detail={},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/jobs/failures", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        jobs = {item["job"]: item for item in payload["jobs"]}
        assert jobs["sync-users"]["failed"] == 2
        assert abs(jobs["sync-users"]["failure_rate"] - 0.667) < 0.001
        assert jobs["sync-reports"]["failed"] == 0

    print("PASS")


if __name__ == "__main__":
    _self_test()