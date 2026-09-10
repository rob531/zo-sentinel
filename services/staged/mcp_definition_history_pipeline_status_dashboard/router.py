from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import mcp_definition_history, CadenceJobRun
from pydantic import BaseModel
from typing import List
import datetime

router = APIRouter()

class JobStatus(BaseModel):
    job: str
    status: str
    started_at: datetime.datetime
    finished_at: datetime.datetime
    rows_affected: int

class PipelineStatus(BaseModel):
    status: str
    last_updated: datetime.datetime
    jobs: List[JobStatus]

@router.get("/api/mcp/definition-history/pipeline-status", response_model=PipelineStatus)
def get_pipeline_status(session: Session = Depends(get_session)):
    latest_job = session.query(CadenceJobRun).order_by(CadenceJobRun.finished_at.desc()).first()
    if not latest_job:
        return PipelineStatus(status="no jobs", last_updated=datetime.datetime.now(), jobs=[])

    jobs = session.query(CadenceJobRun).filter(
        CadenceJobRun.finished_at >= latest_job.finished_at - datetime.timedelta(hours=1)
    ).all()

    job_statuses = []
    for job in jobs:
        job_status = JobStatus(
            job=job.job,
            status=job.status,
            started_at=job.started_at,
            finished_at=job.finished_at,
            rows_affected=job.rows_affected
        )
        job_statuses.append(job_status)

    return PipelineStatus(
        status="success",
        last_updated=latest_job.finished_at,
        jobs=job_statuses
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import pytest

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    def test_get_pipeline_status():
        db = TestingSessionLocal()
        db.execute("CREATE TABLE mcp_definition_history (id INTEGER PRIMARY KEY, definition TEXT)")
        db.execute("CREATE TABLE CadenceJobRun (id INTEGER PRIMARY KEY, job TEXT, status TEXT, started_at TIMESTAMP, finished_at TIMESTAMP, rows_affected INTEGER)")
        db.execute("INSERT INTO CadenceJobRun (job, status, started_at, finished_at, rows_affected) VALUES ('job1', 'success', '2023-01-01 00:00:00', '2023-01-01 00:01:00', 100)")
        db.execute("INSERT INTO CadenceJobRun (job, status, started_at, finished_at, rows_affected) VALUES ('job2', 'failed', '2023-01-01 00:02:00', '2023-01-01 00:03:00', 50)")
        db.execute("INSERT INTO CadenceJobRun (job, status, started_at, finished_at, rows_affected) VALUES ('job3', 'success', '2023-01-01 00:04:00', '2023-01-01 00:05:00', 200)")
        db.commit()

        app.dependency_overrides[get_session] = override_get_session

        response = client.get("/api/mcp/definition-history/pipeline-status")
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) == 3
        assert data["jobs"][0]["status"] == "success"
        print("PASS")

    test_get_pipeline_status()