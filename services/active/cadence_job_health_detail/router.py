# deps: fastapi, pydantic, sqlalchemy
"""cadence_job_health_detail service.

Provides detailed health information for cadence job runs including:
  GET /api/cadence/jobs/{job}/health  -- detailed health for a specific job
  GET /api/cadence/health              -- all jobs with health summary

APP table (cadence_job_runs): via get_session + SQLAlchemy.
Public endpoint (auth=public per the directive).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api/cadence", tags=["cadence_job_health_detail"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class JobRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None
    detail: Optional[str] = None


class RecentFailureOut(BaseModel):
    id: int
    finished_at: Optional[datetime] = None
    detail: Optional[str] = None


class JobHealthMetrics(BaseModel):
    job: str
    total_runs: int = Field(..., description="Total number of runs")
    success_count: int = Field(..., description="Number of successful runs")
    failure_count: int = Field(..., description="Number of failed runs")
    success_rate: float = Field(..., description="Success rate as decimal (0-1)")
    avg_duration_sec: float = Field(..., description="Average duration in seconds")
    p95_duration_sec: float = Field(..., description="95th percentile duration in seconds")
    last_run_at: Optional[datetime] = Field(None, description="Timestamp of last run")
    last_status: Optional[str] = Field(None, description="Status of last run")
    recent_failures: list[RecentFailureOut] = Field(
        default_factory=list, description="Recent failure details"
    )


class SingleJobHealthResponse(BaseModel):
    job: str
    metrics: JobHealthMetrics


class AllJobsHealthResponse(BaseModel):
    jobs: list[JobHealthMetrics]
    total_jobs: int


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #


def _p95(values: list[float]) -> float:
    """Compute linear-interpolation p95 (Postgres percentile_cont equivalent)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = (n - 1) * 0.95
    lower = int(idx)
    upper = lower + 1
    weight = idx - lower
    if upper >= n:
        return sorted_vals[-1]
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


def _compute_duration_sec(row: CadenceJobRun) -> float:
    """Compute duration in seconds from started_at and finished_at."""
    if row.started_at is None or row.finished_at is None:
        return 0.0
    delta = row.finished_at - row.started_at
    return delta.total_seconds()


def _get_job_metrics(session: Session, job: str, failure_limit: int = 10) -> JobHealthMetrics:
    """Compute health metrics for a specific job."""
    stmt = select(CadenceJobRun).where(CadenceJobRun.job == job)
    runs = session.execute(stmt).scalars().all()

    if not runs:
        raise HTTPException(status_code=404, detail=f"No runs found for job '{job}'")

    total_runs = len(runs)
    success_count = sum(1 for r in runs if r.status == "success")
    failure_count = sum(1 for r in runs if r.status == "failure")

    durations = [_compute_duration_sec(r) for r in runs if r.started_at and r.finished_at]
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    p95_duration = _p95(durations) if durations else 0.0

    sorted_runs = sorted(runs, key=lambda r: r.started_at or datetime.min, reverse=True)
    last_run = sorted_runs[0]

    # Recent failures
    failure_runs = [r for r in runs if r.status == "failure"]
    failure_runs.sort(key=lambda r: r.finished_at or datetime.min, reverse=True)
    recent_failures = [
        RecentFailureOut(id=r.id, finished_at=r.finished_at, detail=r.detail)
        for r in failure_runs[:failure_limit]
    ]

    return JobHealthMetrics(
        job=job,
        total_runs=total_runs,
        success_count=success_count,
        failure_count=failure_count,
        success_rate=success_count / total_runs if total_runs > 0 else 0.0,
        avg_duration_sec=round(avg_duration, 2),
        p95_duration_sec=round(p95_duration, 2),
        last_run_at=last_run.started_at,
        last_status=last_run.status,
        recent_failures=recent_failures,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/jobs/{job}/health",
    response_model=SingleJobHealthResponse,
    summary="Get detailed health for a specific job",
    responses={404: {"description": "Job not found"}},
)
def get_job_health(
    job: str,
    failure_limit: int = Query(default=10, ge=1, le=50, description="Max recent failures to return"),
    session: Session = Depends(get_session),
) -> SingleJobHealthResponse:
    """Return detailed health metrics for a named cadence job."""
    metrics = _get_job_metrics(session, job, failure_limit)
    return SingleJobHealthResponse(job=job, metrics=metrics)


@router.get(
    "/health",
    response_model=AllJobsHealthResponse,
    summary="Get health summary for all cadence jobs",
)
def get_all_jobs_health(
    failure_limit: int = Query(default=5, ge=1, le=50, description="Max recent failures per job"),
    session: Session = Depends(get_session),
) -> AllJobsHealthResponse:
    """Return health metrics for all cadence jobs that have at least one run."""
    # Get distinct job names
    job_names_stmt = select(CadenceJobRun.job).distinct()
    job_names = session.execute(job_names_stmt).scalars().all()

    jobs_metrics = []
    for job_name in job_names:
        metrics = _get_job_metrics(session, job_name, failure_limit)
        jobs_metrics.append(metrics)

    # Sort by last_run_at descending
    jobs_metrics.sort(key=lambda m: m.last_run_at or datetime.min, reverse=True)

    return AllJobsHealthResponse(jobs=jobs_metrics, total_jobs=len(jobs_metrics))


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import timedelta

    from app.db import Base, get_session as real_get_session
    from app.models import CadenceJobRun

    # Build local FastAPI app (not app.main:app) for unit self-test
    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    base_ts = datetime(2024, 1, 1, 0, 0, 0)
    # (job, status, start_offset_s, end_offset_s, rows_affected, detail)
    seed = [
        ("scan_servers", "success", 0, 60, 50, "scan complete"),
        ("scan_servers", "success", 100, 170, 55, "scan complete"),
        ("scan_servers", "failure", 200, 250, 0, "timeout"),
        ("scan_servers", "failure", 300, 340, 0, "connection error"),
        ("scan_servers", "success", 400, 455, 52, "scan complete"),
        ("ingest_vuln", "success", 10, 120, 200, "ingest ok"),
        ("ingest_vuln", "failure", 200, 280, 0, "feed unavailable"),
        ("score_batch", "success", 50, 150, 100, "batch scored"),
        ("score_batch", "success", 200, 310, 105, "batch scored"),
    ]

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[real_get_session] = get_test_session

    with TestSessionLocal() as db:
        for job, status, start_s, end_s, rows, detail in seed:
            db.add(CadenceJobRun(
                job=job,
                status=status,
                started_at=base_ts + timedelta(seconds=start_s),
                finished_at=base_ts + timedelta(seconds=end_s),
                rows_affected=rows,
                detail=detail,
            ))
        db.commit()

    client = TestClient(app)

    # Test 1: get_job_health for scan_servers
    resp1 = client.get("/api/cadence/jobs/scan_servers/health")
    assert resp1.status_code == 200, f"scan_servers health failed: {resp1.text}"
    data1 = resp1.json()
    assert data1["job"] == "scan_servers"
    assert data1["metrics"]["total_runs"] == 5
    assert data1["metrics"]["success_count"] == 3
    assert data1["metrics"]["failure_count"] == 2
    assert abs(data1["metrics"]["success_rate"] - 0.6) < 0.01
    assert len(data1["metrics"]["recent_failures"]) == 2
    assert data1["metrics"]["recent_failures"][0]["detail"] == "connection error"
    assert data1["metrics"]["recent_failures"][1]["detail"] == "timeout"

    # Test 2: get_job_health for ingest_vuln
    resp2 = client.get("/api/cadence/jobs/ingest_vuln/health")
    assert resp2.status_code == 200, f"ingest_vuln health failed: {resp2.text}"
    data2 = resp2.json()
    assert data2["metrics"]["total_runs"] == 2
    assert data2["metrics"]["success_rate"] == 0.5

    # Test 3: 404 for unknown job
    resp3 = client.get("/api/cadence/jobs/nonexistent/health")
    assert resp3.status_code == 404, f"expected 404, got {resp3.status_code}"

    # Test 4: get_all_jobs_health
    resp4 = client.get("/api/cadence/health")
    assert resp4.status_code == 200, f"all jobs health failed: {resp4.text}"
    data4 = resp4.json()
    assert data4["total_jobs"] == 3
    jobs_by_name = {j["job"]: j for j in data4["jobs"]}
    assert "scan_servers" in jobs_by_name
    assert "ingest_vuln" in jobs_by_name
    assert "score_batch" in jobs_by_name
    assert jobs_by_name["score_batch"]["success_rate"] == 1.0
    assert len(jobs_by_name["scan_servers"]["recent_failures"]) == 2

    # Test 5: failure_limit parameter
    resp5 = client.get("/api/cadence/jobs/scan_servers/health?failure_limit=1")
    assert resp5.status_code == 200
    data5 = resp5.json()
    assert len(data5["metrics"]["recent_failures"]) == 1

    print("PASS")
