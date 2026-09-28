"""cadence_job_runs_health contract.

Provides an endpoint that aggregates health information for Cadence jobs.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real application data layer imports (must not be re‑implemented)
from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class RunSummary(BaseModel):
    id: int
    status: str
    started_at: Optional[datetime.datetime] = None
    finished_at: Optional[datetime.datetime] = None
    rows_affected: Optional[int] = None


class JobHealth(BaseModel):
    job_name: str
    last_status: Optional[str] = None
    run_count: int
    success_rate_pct: Optional[float] = None
    avg_duration_s: Optional[float] = None
    last_runs: List[RunSummary]


class JobsHealthResponse(BaseModel):
    jobs: List[JobHealth]


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/cadence/jobs/health",
    response_model=JobsHealthResponse,
    summary="Health summary for all Cadence jobs",
)
def get_cadence_jobs_health(db: Session = Depends(get_session)):
    """Aggregate Cadence job run health per job."""
    # Pull all runs ordered by job then newest first
    stmt = select(CadenceJobRun).order_by(CadenceJobRun.job, CadenceJobRun.started_at.desc())
    runs = db.execute(stmt).scalars().all()

    jobs: dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        jobs.setdefault(run.job, []).append(run)

    result_jobs: List[JobHealth] = []
    for job_name, job_runs in jobs.items():
        # Runs are already newest‑first
        last_run = job_runs[0] if job_runs else None
        last_status = last_run.status if last_run else None

        run_count = len(job_runs)
        success_cnt = sum(1 for r in job_runs if r.status.lower() == "success")
        success_rate = (success_cnt / run_count * 100) if run_count else None

        # Average duration (seconds) for runs that have both timestamps
        durations = [
            (r.finished_at - r.started_at).total_seconds()
            for r in job_runs
            if r.started_at and r.finished_at
        ]
        avg_duration = (sum(durations) / len(durations)) if durations else None

        # Summaries of the last five runs
        last_five = job_runs[:5]
        last_runs_summaries = [
            RunSummary(
                id=r.id,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                rows_affected=r.rows_affected,
            )
            for r in last_five
        ]

        result_jobs.append(
            JobHealth(
                job_name=job_name,
                last_status=last_status,
                run_count=run_count,
                success_rate_pct=success_rate,
                avg_duration_s=avg_duration,
                last_runs=last_runs_summaries,
            )
        )

    return JobsHealthResponse(jobs=result_jobs)


# --------------------------------------------------------------------------- #
# Self‑test (executed with `python -m services.staged.cadence_job_runs_health.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a minimal FastAPI app for the test
    test_app = FastAPI()
    test_app.include_router(router)

    # In‑memory SQLite engine (StaticPool ensures the same connection is reused)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables for CadenceJobRun only
    CadenceJobRun.__table__.metadata.create_all(bind=engine)

    # Dependency override to use the test session
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed data: 3 jobs, 5 runs each, mixed success/failure
    now = datetime.datetime.utcnow()
    seed = []
    for idx, job_name in enumerate(["jobA", "jobB", "jobC"], start=1):
        for run_idx in range(5):
            started = now - datetime.timedelta(minutes=idx * 10 + run_idx * 2)
            finished = started + datetime.timedelta(seconds=30 + run_idx * 5)
            status = "success" if run_idx % 2 == 0 else "failure"
            seed.append(
                CadenceJobRun(
                    job=job_name,
                    status=status,
                    started_at=started,
                    finished_at=finished,
                    rows_affected=run_idx * 10,
                    detail={},  # JSON column; empty dict is fine
                )
            )
    # Insert seed data
    with SessionLocal() as db:
        db.add_all(seed)
        db.commit()

    client = TestClient(test_app)

    resp = client.get("/api/cadence/jobs/health")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    jobs = data.get("jobs", [])
    if len(jobs) != 3:
        print(f"FAIL: expected 3 jobs, got {len(jobs)}", file=sys.stderr)
        sys.exit(1)

    # Verify known success rate for jobA (runs 0,2,4 are successes => 3/5 = 60.0%)
    job_a = next((j for j in jobs if j["job_name"] == "jobA"), None)
    if not job_a:
        print("FAIL: jobA missing", file=sys.stderr)
        sys.exit(1)

    expected_rate = 60.0
    actual_rate = round(job_a.get("success_rate_pct", 0), 1)
    if actual_rate != expected_rate:
        print(
            f"FAIL: jobA success_rate_pct expected {expected_rate}, got {actual_rate}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("PASS")
    sys.exit(0)