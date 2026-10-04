from datetime import datetime

from fastapi import FastAPI, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, CadenceJobRun

from pydantic import BaseModel


class CadenceJobRunResponse(BaseModel):
    job: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows_affected: int | None = None


class CadenceJobRunListResponse(BaseModel):
    job_runs: list[CadenceJobRunResponse]


the_app = FastAPI()


@the_app.get("/api/cadence", response_model=CadenceJobRunListResponse)
def get_cadence_job_runs(
    job: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> CadenceJobRunListResponse:
    stmt = select(CadenceJobRun)
    if job is not None:
        stmt = stmt.where(CadenceJobRun.job == job)
    if status is not None:
        stmt = stmt.where(CadenceJobRun.status == status)
    stmt = stmt.order_by(CadenceJobRun.started_at)
    result = session.execute(stmt).scalars().all()
    return CadenceJobRunListResponse(
        job_runs=[
            CadenceJobRunResponse(
                job=r.job,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                rows_affected=r.rows_affected,
            )
            for r in result
        ]
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        session.add(
            CadenceJobRun(
                job="test_job_1",
                status="success",
                started_at=datetime(2024, 1, 1, 10, 0, 0),
                finished_at=datetime(2024, 1, 1, 10, 5, 0),
                rows_affected=100,
            )
        )
        session.add(
            CadenceJobRun(
                job="test_job_2",
                status="failed",
                started_at=datetime(2024, 1, 1, 11, 0, 0),
                finished_at=datetime(2024, 1, 1, 11, 2, 0),
                rows_affected=0,
            )
        )
        session.commit()

    def override_get_session():
        with Session(engine) as s:
            yield s

    the_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(the_app)
    response = client.get("/api/cadence")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "job_runs" in data
    job_runs = data["job_runs"]
    assert len(job_runs) == 2
    job_names = {jr["job"] for jr in job_runs}
    assert job_names == {"test_job_1", "test_job_2"}
    statuses = {jr["status"] for jr in job_runs}
    assert statuses == {"success", "failed"}

    print("PASS")