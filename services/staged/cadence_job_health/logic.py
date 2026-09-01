from datetime import datetime
from typing import List, Optional

from app.db import get_session
from app.models import CadenceJobRun
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text


class JobHealthMetric(BaseModel):
    job: str
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    rows_affected: int


class JobHealthReport(BaseModel):
    metrics: List[JobHealthMetric]


def get_cadence_job_health(
    job_name: Optional[str] = None,
    session=Depends(get_session),
) -> JobHealthReport:
    where_clause = ""
    params = {}
    if job_name:
        where_clause = "WHERE job = :job_name"
        params["job_name"] = job_name

    query = text(f"""
        SELECT
            job,
            status,
            MIN(started_at) as started_at,
            MAX(finished_at) as finished_at,
            SUM(rows_affected) as rows_affected
        FROM cadence_job_runs
        {where_clause}
        GROUP BY job, status
        ORDER BY job, status
    """)

    result = session.execute(query, params)
    rows = result.fetchall()

    metrics = [
        JobHealthMetric(
            job=row.job,
            status=row.status,
            started_at=row.started_at,
            finished_at=row.finished_at,
            rows_affected=row.rows_affected or 0,
        )
        for row in rows
    ]

    return JobHealthReport(metrics=metrics)


if __name__ == "__main__":
    import pytest
    from fastapi import FastAPI
    from sqlalchemy import StaticPool, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import get_session as original_get_session
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()

    @app.get("/api/cadence/jobs/health")
    def health_endpoint(job: str = None, session=Depends(get_session)):
        return get_cadence_job_health(job_name=job, session=session)

    with TestingSessionLocal() as db:
        db.execute(text("""
            INSERT INTO cadence_job_runs (job, status, started_at, finished_at, rows_affected)
            VALUES
                ('test_job_1', 'completed', '2024-01-01 10:00:00', '2024-01-01 10:05:00', 100),
                ('test_job_1', 'completed', '2024-01-01 11:00:00', '2024-01-01 11:03:00', 150),
                ('test_job_2', 'failed', '2024-01-01 12:00:00', '2024-01-01 12:01:00', 0)
        """))
        db.commit()

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/cadence/jobs/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    metrics = {m["job"] + ":" + m["status"]: m for m in data["metrics"]}

    j1_completed = metrics.get("test_job_1:completed")
    assert j1_completed is not None, "test_job_1:completed not found"
    assert j1_completed["rows_affected"] == 250, f"Expected 250, got {j1_completed['rows_affected']}"

    j2_failed = metrics.get("test_job_2:failed")
    assert j2_failed is not None, "test_job_2:failed not found"
    assert j2_failed["rows_affected"] == 0, f"Expected 0, got {j2_failed['rows_affected']}"

    print("PASS")