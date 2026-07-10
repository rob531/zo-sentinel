from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter()

class JobRun(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    rows_affected: Optional[int]
    detail: Optional[str]

class StaleJob(BaseModel):
    job: str
    last_run: datetime
    age_seconds: int

class JobResponse(BaseModel):
    jobs: List[JobRun]
    stale_jobs: List[StaleJob]

@router.get("/cadence/jobs/recent", response_model=JobResponse)
async def get_recent_job_runs(db: Session = Depends(get_session)):
    # Get recent job runs
    recent_jobs = db.query(CadenceJobRun).order_by(desc(CadenceJobRun.started_at)).limit(50).all()

    # Get all jobs for stale detection
    all_jobs = db.query(CadenceJobRun.job, CadenceJobRun.started_at).all()

    # Process recent jobs
    jobs = []
    for job in recent_jobs:
        jobs.append({
            "id": job.id,
            "job": job.job,
            "status": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "rows_affected": job.rows_affected,
            "detail": job.detail
        })

    # Detect stale jobs (older than 300 seconds)
    stale_jobs = []
    now = datetime.utcnow()
    for job, last_run in all_jobs:
        age = (now - last_run).total_seconds()
        if age > 300:
            stale_jobs.append({
                "job": job,
                "last_run": last_run,
                "age_seconds": int(age)
            })

    return {"jobs": jobs, "stale_jobs": stale_jobs}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        CadenceJobRun(
            job="test_job_1",
            status="completed",
            started_at=datetime.utcnow() - timedelta(seconds=100),
            finished_at=datetime.utcnow() - timedelta(seconds=50),
            rows_affected=10,
            detail="Test job 1"
        ),
        CadenceJobRun(
            job="test_job_2",
            status="completed",
            started_at=datetime.utcnow() - timedelta(seconds=400),
            finished_at=datetime.utcnow() - timedelta(seconds=350),
            rows_affected=5,
            detail="Test job 2"
        ),
        CadenceJobRun(
            job="test_job_3",
            status="failed",
            started_at=datetime.utcnow() - timedelta(seconds=200),
            finished_at=None,
            rows_affected=None,
            detail="Test job 3 failed"
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/cadence/jobs/recent")
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "jobs" in data
    assert "stale_jobs" in data
    assert len(data["jobs"]) == 3
    assert len(data["stale_jobs"]) == 1
    assert data["stale_jobs"][0]["job"] == "test_job_2"
    assert data["stale_jobs"][0]["age_seconds"] > 300

    print("PASS")