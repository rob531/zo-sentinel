from datetime import datetime
from typing import Optional
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


class JobRunResponse(BaseModel):
    job: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None

    class Config:
        from_attributes = True


class CadenceMonitorResponse(BaseModel):
    job_runs: list[JobRunResponse]


def get_cadence_jobs(
    session: Session,
    job_name: Optional[str] = None,
    status: Optional[str] = None,
) -> CadenceMonitorResponse:
    query = select(CadenceJobRun)

    filters = []
    if job_name:
        filters.append(CadenceJobRun.job == job_name)
    if status:
        filters.append(CadenceJobRun.status == status)

    if filters:
        query = query.where(or_(*filters))

    result = session.execute(query).scalars().all()

    job_runs = [
        JobRunResponse(
            job=run.job,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            rows_affected=run.rows_affected,
        )
        for run in result
    ]

    return CadenceMonitorResponse(job_runs=job_runs)


if __name__ == "__main__":
    from fastapi import FastAPI
    from main import app as main_app

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models.base import Base
    Base.metadata.create_all(bind=test_engine)

    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence")
    def get_cadence_endpoint(
        job: Optional[str] = None,
        status: Optional[str] = None,
        session: Session = Depends(get_session),
    ):
        return get_cadence_jobs(session, job_name=job, status=status)

    with test_engine.connect() as conn:
        from app.models import CadenceJobRun

        conn.execute(
            CadenceJobRun.__table__.insert(),
            {
                "job": "test_job_1",
                "status": "completed",
                "started_at": datetime(2024, 1, 1, 10, 0, 0),
                "finished_at": datetime(2024, 1, 1, 10, 5, 0),
                "rows_affected": 100,
                "detail": "First test job",
            },
        )
        conn.execute(
            CadenceJobRun.__table__.insert(),
            {
                "job": "test_job_2",
                "status": "failed",
                "started_at": datetime(2024, 1, 1, 11, 0, 0),
                "finished_at": datetime(2024, 1, 1, 11, 2, 0),
                "rows_affected": 0,
                "detail": "Second test job",
            },
        )
        conn.commit()

    client = test_app.test_client
    response = client.get("/api/cadence")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "job_runs" in data, "Missing job_runs in response"

    jobs = data["job_runs"]
    assert len(jobs) == 2, f"Expected 2 jobs, got {len(jobs)}"

    job_names = {j["job"] for j in jobs}
    assert "test_job_1" in job_names, "test_job_1 not found in response"
    assert "test_job_2" in job_names, "test_job_2 not found in response"

    statuses = {j["status"] for j in jobs}
    assert "completed" in statuses, "completed status not found"
    assert "failed" in statuses, "failed status not found"

    print("PASS")