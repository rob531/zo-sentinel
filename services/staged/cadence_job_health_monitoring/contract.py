"""
services.staged.cadence_job_health_monitoring.contract

FastAPI contract for Cadence job health monitoring.

Provides:
- GET /metrics               → aggregated metrics for all jobs
- GET /metrics/{job_name}    → metrics for a specific job
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

# Real data layer – must be used in production code.
from app.db import get_session
from app.models import CadenceJobRun, Base  # Base is the declarative base used by the app.

router = APIRouter(prefix="/metrics", tags=["cadence_job_health"])


class JobMetrics(BaseModel):
    """Aggregated health metrics for a Cadence job."""
    job: str = Field(..., description="Job identifier")
    total_runs: int = Field(..., description="Number of runs recorded")
    success_rate: float = Field(..., description="Proportion of successful runs")
    avg_duration_seconds: float = Field(..., description="Average run duration in seconds")
    avg_rows_affected: float = Field(..., description="Average rows affected per run")


def _aggregate_query(session: Session):
    """Base aggregation query used by both endpoints."""
    duration_seconds = func.strftime(
        "%s", CadenceJobRun.finished_at
    ) - func.strftime("%s", CadenceJobRun.started_at)
    return (
        session.query(
            CadenceJobRun.job.label("job"),
            func.count(CadenceJobRun.id).label("total"),
            func.sum(
                case((CadenceJobRun.status == "success", 1), else_=0)
            ).label("success_count"),
            func.avg(duration_seconds).label("avg_duration"),
            func.avg(CadenceJobRun.rows_affected).label("avg_rows"),
        )
        .group_by(CadenceJobRun.job)
    )


@router.get("/", response_model=List[JobMetrics])
def get_all_metrics(session: Session = Depends(get_session)):
    """Return aggregated metrics for every job."""
    rows = _aggregate_query(session).all()
    return [
        JobMetrics(
            job=row.job,
            total_runs=row.total,
            success_rate=row.success_count / row.total if row.total else 0.0,
            avg_duration_seconds=row.avg_duration or 0.0,
            avg_rows_affected=row.avg_rows or 0.0,
        )
        for row in rows
    ]


@router.get("/{job_name}", response_model=JobMetrics)
def get_job_metrics(job_name: str, session: Session = Depends(get_session)):
    """Return aggregated metrics for a single job."""
    sub = _aggregate_query(session).filter(CadenceJobRun.job == job_name).subquery()
    stmt = select(
        sub.c.job,
        sub.c.total,
        sub.c.success_count,
        sub.c.avg_duration,
        sub.c.avg_rows,
    )
    result = session.execute(stmt).first()
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    total, success_count, avg_duration, avg_rows = (
        result.total,
        result.success_count,
        result.avg_duration,
        result.avg_rows,
    )
    return JobMetrics(
        job=result.job,
        total_runs=total,
        success_rate=success_count / total if total else 0.0,
        avg_duration_seconds=avg_duration or 0.0,
        avg_rows_affected=avg_rows or 0.0,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.cadence_job_health_monitoring.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build a temporary in‑memory SQLite DB that mirrors the real schema.
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestSession = sessionmaker(bind=engine)

    # Dependency override that yields sessions from the temporary DB.
    def get_test_session() -> Session:
        with TestSession() as sess:
            yield sess

    # ------------------------------------------------------------------- #
    # Seed the DB with deterministic data.
    # ------------------------------------------------------------------- #
    with TestSession() as sess:
        runs = [
            CadenceJobRun(
                id=1,
                job="alpha",
                status="success",
                detail="run 1",
                started_at=datetime(2023, 1, 1, 12, 0, 0),
                finished_at=datetime(2023, 1, 1, 12, 0, 5),
                rows_affected=10,
            ),
            CadenceJobRun(
                id=2,
                job="alpha",
                status="failure",
                detail="run 2",
                started_at=datetime(2023, 1, 2, 13, 0, 0),
                finished_at=datetime(2023, 1, 2, 13, 0, 8),
                rows_affected=5,
            ),
            CadenceJobRun(
                id=3,
                job="beta",
                status="success",
                detail="run 3",
                started_at=datetime(2023, 1, 3, 14, 0, 0),
                finished_at=datetime(2023, 1, 3, 14, 0, 3),
                rows_affected=20,
            ),
        ]
        sess.add_all(runs)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Assemble the FastAPI app with the router and the test overrides.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Validate the `/metrics` endpoint (all jobs).
    # ------------------------------------------------------------------- #
    resp_all = client.get("/metrics/")
    assert resp_all.status_code == 200, f"/metrics returned {resp_all.status_code}"
    data_all = resp_all.json()
    assert isinstance(data_all, list) and len(data_all) == 2, "Unexpected number of jobs"

    # Expected calculations:
    # alpha: total=2, success=1 → success_rate=0.5,
    #        avg_duration = (5 + 8) / 2 = 6.5 seconds,
    #        avg_rows = (10 + 5) / 2 = 7.5
    # beta:  total=1, success=1 → success_rate=1.0,
    #        avg_duration = 3 seconds,
    #        avg_rows = 20
    expected = {
        "alpha": {"total_runs": 2, "success_rate": 0.5, "avg_duration_seconds": 6.5, "avg_rows_affected": 7.5},
        "beta": {"total_runs": 1, "success_rate": 1.0, "avg_duration_seconds": 3.0, "avg_rows_affected": 20.0},
    }
    for job in data_all:
        name = job["job"]
        exp = expected[name]
        assert job["total_runs"] == exp["total_runs"]
        assert abs(job["success_rate"] - exp["success_rate"]) < 1e-6
        assert abs(job["avg_duration_seconds"] - exp["avg_duration_seconds"]) < 1e-6
        assert abs(job["avg_rows_affected"] - exp["avg_rows_affected"]) < 1e-6

    # ------------------------------------------------------------------- #
    # Validate the `/metrics/{job}` endpoint (single job).
    # ------------------------------------------------------------------- #
    resp_alpha = client.get("/metrics/alpha")
    assert resp_alpha.status_code == 200, f"/metrics/alpha returned {resp_alpha.status_code}"
    job_alpha = resp_alpha.json()
    assert job_alpha["job"] == "alpha"
    assert job_alpha["total_runs"] == 2
    assert abs(job_alpha["success_rate"] - 0.5) < 1e-6
    assert abs(job_alpha["avg_duration_seconds"] - 6.5) < 1e-6
    assert abs(job_alpha["avg_rows_affected"] - 7.5) < 1e-6

    resp_beta = client.get("/metrics/beta")
    assert resp_beta.status_code == 200, f"/metrics/beta returned {resp_beta.status_code}"
    job_beta = resp_beta.json()
    assert job_beta["job"] == "beta"
    assert job_beta["total_runs"] == 1
    assert abs(job_beta["success_rate"] - 1.0) < 1e-6
    assert abs(job_beta["avg_duration_seconds"] - 3.0) < 1e-6
    assert abs(job_beta["avg_rows_affected"] - 20.0) < 1e-6

    print("PASS")