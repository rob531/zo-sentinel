from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["cadence"])


class JobHealth(BaseModel):
    job: str
    last_run_at: Optional[datetime]
    last_status: Optional[str]
    avg_duration_s: Optional[float]
    success_rate_7d: Optional[float]
    total_runs_7d: int


class HealthResponse(BaseModel):
    jobs: List[JobHealth]


@router.get("/cadence/jobs/health", response_model=HealthResponse)
def health(db: Session = Depends(get_session)) -> HealthResponse:
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)

    results = (
        db.query(
            CadenceJobRun.job,
            func.max(CadenceJobRun.started_at).label("last_run_at"),
            func.max(CadenceJobRun.id).label("last_id"),
            func.count().label("total_runs_7d"),
            func.sum(case((CadenceJobRun.status == "success", 1), else_=0)).label("success_count"),
            func.avg(
                func.extract("epoch", CadenceJobRun.finished_at - CadenceJobRun.started_at)
            ).label("avg_duration_s"),
        )
        .filter(CadenceJobRun.started_at >= seven_days_ago)
        .group_by(CadenceJobRun.job)
        .all()
    )

    last_status_sub = (
        db.query(
            CadenceJobRun.job,
            CadenceJobRun.status.label("last_status"),
        )
        .join(
            db.query(
                CadenceJobRun.job,
                func.max(CadenceJobRun.started_at).label("max_started"),
            )
            .filter(CadenceJobRun.started_at >= seven_days_ago)
            .group_by(CadenceJobRun.job)
            .subquery(),
            (CadenceJobRun.job == CadenceJobRun.job) & (CadenceJobRun.started_at == CadenceJobRun.started_at),
        )
        .subquery()
    )

    jobs_list = []
    for r in results:
        success_rate = float(r.success_count) / float(r.total_runs_7d) if r.total_runs_7d > 0 else None
        avg_dur = float(r.avg_duration_s) if r.avg_duration_s is not None else None

        last_status = (
            db.query(CadenceJobRun.status)
            .filter(CadenceJobRun.job == r.job, CadenceJobRun.started_at == r.last_run_at)
            .scalar()
        )

        jobs_list.append(
            JobHealth(
                job=r.job,
                last_run_at=r.last_run_at,
                last_status=last_status,
                avg_duration_s=avg_dur,
                success_rate_7d=success_rate,
                total_runs_7d=r.total_runs_7d,
            )
        )

    return HealthResponse(jobs=jobs_list)


if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)

    now = datetime.utcnow()
    today = now.replace(hour=10, minute=0, second=0, microsecond=0)
    six_days_ago = today - timedelta(days=6)

    session = TestingSessionLocal()

    jobs_data = [
        ("daemon_alpha", today, "success", 5.0, {"rows": 100}),
        ("daemon_alpha", six_days_ago, "failure", 30.0, {"error": "timeout"}),
        ("daemon_beta", today, "success", 2.0, {"rows": 50}),
        ("daemon_beta", six_days_ago, "success", 3.0, {"rows": 75}),
        ("daemon_gamma", today, "failure", 15.0, {"error": "crash"}),
        ("daemon_gamma", six_days_ago, "failure", 20.0, {"error": "OOM"}),
        ("daemon_delta", today, "success", 1.0, {"rows": 25}),
        ("daemon_delta", six_days_ago, "success", 1.5, {"rows": 30}),
    ]

    for job, started, status, duration, detail in jobs_data:
        finished = started + timedelta(seconds=duration)
        session.add(
            CadenceJobRun(
                job=job,
                started_at=started,
                finished_at=finished,
                status=status,
                rows_affected=detail.get("rows", 0),
                detail=detail,
            )
        )

    session.commit()
    session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(test_app)
    response = client.get("/api/cadence/jobs/health")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "jobs" in data, f"Expected 'jobs' key in response, got {data.keys()}"

    jobs = data["jobs"]
    assert len(jobs) > 0, "Expected at least one job in response"

    found_partial_success = False
    for job in jobs:
        sr = job.get("success_rate_7d")
        if sr is not None and sr < 1.0:
            found_partial_success = True
            break

    assert found_partial_success, f"Expected at least one job with success_rate < 1.0, got {[j.get('success_rate_7d') for j in jobs]}"

    print("PASS")