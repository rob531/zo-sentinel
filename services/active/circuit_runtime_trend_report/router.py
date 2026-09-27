# deps: fastapi, pydantic, sqlalchemy
"""Circuit Runtime Trend Report API

Aggregates cadence job run statistics over a configurable time window and
returns per-job runtime trends (avg duration, success rate, rows throughput).

Endpoints:
  GET /api/circuit_runtime_trend_report
      Query params: window_days (int, default 7)
      Returns per-job runtime trend metrics for the window.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["circuit_runtime_trend_report"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class JobTrendRow(BaseModel):
    job: str = Field(..., description="Job name")
    run_count: int = Field(..., description="Total runs in the window")
    avg_duration_s: float = Field(..., description="Average run duration in seconds")
    success_rate_pct: float = Field(..., description="Success rate as a percentage")
    rows_per_run_avg: float = Field(..., description="Average rows affected per run")
    last_run_at: Optional[datetime] = Field(
        None, description="Timestamp of the most recent run"
    )


class RuntimeTrendReportResponse(BaseModel):
    window_days: int = Field(..., description="Aggregation window in days")
    jobs: List[JobTrendRow] = Field(..., description="Per-job trend metrics")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/circuit_runtime_trend_report",
    response_model=RuntimeTrendReportResponse,
    summary="Get cadence job runtime trend report",
)
def get_runtime_trend_report(
    window_days: int = Query(
        default=7, ge=1, le=365,
        description="Number of days to look back for run statistics.",
    ),
    db: Session = Depends(get_session),
) -> RuntimeTrendReportResponse:
    """Return aggregated runtime metrics for each cadence job over the past
    *window_days* days."""
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    # Fetch runs in window ordered by started_at so grouping is easy
    runs = (
        db.query(CadenceJobRun)
        .filter(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.job, CadenceJobRun.started_at.desc())
        .all()
    )

    # Group by job
    by_job: dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        by_job.setdefault(run.job, []).append(run)

    jobs: List[JobTrendRow] = []
    for job_name, job_runs in by_job.items():
        run_count = len(job_runs)
        success_count = sum(1 for r in job_runs if r.status == "success")

        # Durations: only for runs that finished
        durations: List[float] = []
        for r in job_runs:
            if r.finished_at and r.started_at:
                delta = r.finished_at - r.started_at
                durations.append(delta.total_seconds())

        avg_duration = sum(durations) / len(durations) if durations else 0.0
        rows_vals = [r.rows_affected for r in job_runs if r.rows_affected is not None]
        rows_avg = sum(rows_vals) / len(rows_vals) if rows_vals else 0.0
        last_run = max((r.finished_at for r in job_runs if r.finished_at), default=None)
        success_rate = (success_count / run_count) * 100.0 if run_count else 0.0

        jobs.append(
            JobTrendRow(
                job=job_name,
                run_count=run_count,
                avg_duration_s=round(avg_duration, 2),
                success_rate_pct=round(success_rate, 2),
                rows_per_run_avg=round(rows_avg, 2),
                last_run_at=last_run,
            )
        )

    return RuntimeTrendReportResponse(window_days=window_days, jobs=jobs)


# ---------------------------------------------------------------------------
# Self‑test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session as real_get_session
    from app.models import CadenceJobRun  # registers table on Base.metadata

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    base_ts = datetime.utcnow() - timedelta(days=3)

    # Seed: 3 jobs, 5 runs each (mix of success/failure)
    # (job, status, start_offset_s, end_offset_s, rows_affected)
    seed = [
        # job_a: 5 success runs, all fast (~15s each)
        ("job_a", "success", 0, 15, 100),
        ("job_a", "success", 60, 75, 110),
        ("job_a", "success", 120, 135, 90),
        ("job_a", "success", 180, 195, 120),
        ("job_a", "success", 240, 255, 105),
        # job_b: 3 success, 2 failed
        ("job_b", "success", 10, 55, 200),
        ("job_b", "success", 70, 130, 220),
        ("job_b", "failed", 140, 200, 0),
        ("job_b", "success", 210, 260, 190),
        ("job_b", "failed", 270, 310, 0),
        # job_c: 1 success, 4 failed
        ("job_c", "failed", 5, 25, 0),
        ("job_c", "failed", 35, 60, 0),
        ("job_c", "failed", 65, 95, 0),
        ("job_c", "failed", 100, 130, 0),
        ("job_c", "success", 135, 160, 50),
    ]

    with TestSessionLocal() as db:
        for job_name, status, start_s, end_s, rows_affected in seed:
            db.add(CadenceJobRun(
                job=job_name,
                status=status,
                started_at=base_ts + timedelta(seconds=start_s),
                finished_at=base_ts + timedelta(seconds=end_s),
                rows_affected=rows_affected,
            ))
        db.commit()

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[real_get_session] = get_test_session

    client = TestClient(app)

    # Happy path
    resp = client.get("/api/circuit_runtime_trend_report?window_days=7")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    payload = resp.json()
    assert "jobs" in payload, f"Expected 'jobs' in response, got {payload}"
    assert payload["window_days"] == 7

    jobs = {j["job"]: j for j in payload["jobs"]}
    assert len(jobs) == 3, f"Expected 3 jobs, got {len(jobs)}"

    # job_a: all success, avg duration ~15s
    assert jobs["job_a"]["run_count"] == 5
    assert jobs["job_a"]["success_rate_pct"] == 100.0, "job_a should be all-success"
    assert jobs["job_a"]["avg_duration_s"] == 15.0, "job_a avg duration should be 15s"

    # job_b: 3/5 success = 60%
    assert jobs["job_b"]["run_count"] == 5
    assert jobs["job_b"]["success_rate_pct"] == 60.0, "job_b should be 60% success"

    # job_c: 1/5 success = 20%
    assert jobs["job_c"]["run_count"] == 5
    assert jobs["job_c"]["success_rate_pct"] == 20.0, "job_c should be 20% success"

    # Window boundary: 0-day window should return empty
    resp_empty = client.get("/api/circuit_runtime_trend_report?window_days=0")
    # window_days=0 is invalid (ge=1), FastAPI returns 422
    assert resp_empty.status_code == 422, f"Expected 422 for window_days=0, got {resp_empty.status_code}"

    for name, job in sorted(jobs.items()):
        print(
            f"  - {name}: runs={job['run_count']} "
            f"success={job['success_rate_pct']:.0f}% "
            f"avg_dur={job['avg_duration_s']}s"
        )
    print("PASS")
