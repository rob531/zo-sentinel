from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceJobRunResponse(BaseModel):
    job: str
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    rows_affected: int

    class Config:
        from_attributes = True


class CadenceJobRunsResponse(BaseModel):
    job_runs: List[CadenceJobRunResponse]


@router.get("/cadence", response_model=CadenceJobRunsResponse)
def get_cadence(
    job: Optional[str] = Query(None, description="Filter by job name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    session: Session = Depends(get_session),
) -> CadenceJobRunsResponse:
    query = session.query(CadenceJobRun)

    if job is not None:
        query = query.filter(CadenceJobRun.job == job)
    if status is not None:
        query = query.filter(CadenceJobRun.status == status)

    query = query.order_by(CadenceJobRun.started_at.desc())

    job_runs = query.all()

    return CadenceJobRunsResponse(
        job_runs=[
            CadenceJobRunResponse(
                job=jr.job,
                status=jr.status,
                started_at=jr.started_at,
                finished_at=jr.finished_at,
                rows_affected=jr.rows_affected,
            )
            for jr in job_runs
        ]
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)

    with engine.connect() as conn:
        conn.execute(
            CadenceJobRun.__table__.insert(),
            [
                {
                    "job": "test_sync_job",
                    "status": "completed",
                    "started_at": datetime(2024, 1, 1, 10, 0, 0),
                    "finished_at": datetime(2024, 1, 1, 10, 5, 0),
                    "rows_affected": 100,
                    "detail": "Test sync completed",
                },
                {
                    "job": "test_pipeline_job",
                    "status": "failed",
                    "started_at": datetime(2024, 1, 1, 11, 0, 0),
                    "finished_at": datetime(2024, 1, 1, 11, 2, 0),
                    "rows_affected": 0,
                    "detail": "Test pipeline failed",
                },
            ],
        )
        conn.commit()

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/api/cadence")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "job_runs" in data
    assert len(data["job_runs"]) == 2

    jobs_returned = {jr["job"] for jr in data["job_runs"]}
    assert "test_sync_job" in jobs_returned
    assert "test_pipeline_job" in jobs_returned

    print("PASS")