import datetime
from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must stay unchanged)
from app.db import get_session
from app.models import CadenceJobRun, Base

router = APIRouter(prefix="/api")


class JobAuditItem(BaseModel):
    name: str = Field(..., description="Job name")
    total_runs: int = Field(..., description="Total number of runs")
    success_count: int = Field(..., description="Number of successful runs")
    failure_count: int = Field(..., description="Number of failed runs")
    success_rate_pct: float = Field(..., description="Success rate percentage")
    avg_duration_sec: Optional[float] = Field(
        None, description="Average duration of runs in seconds"
    )
    last_run_at: Optional[datetime.datetime] = Field(
        None, description="Timestamp of the most recent run"
    )
    last_status: Optional[str] = Field(
        None, description="Status of the most recent run"
    )


class JobsAuditResponse(BaseModel):
    jobs: List[JobAuditItem] = Field(..., description="List of job audit entries")


@router.get(
    "/cadence/jobs/audit",
    response_model=JobsAuditResponse,
    summary="Audit Cadence job runs",
)
def get_cadence_jobs_audit(session: Session = Depends(get_session)):
    """
    Compute audit statistics for each Cadence job.
    """
    # Pull all runs
    stmt = select(
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
    )
    rows = session.execute(stmt).all()

    if not rows:
        raise HTTPException(status_code=404, detail="No Cadence job runs found")

    # Aggregate per job
    agg = defaultdict(
        lambda: {
            "total": 0,
            "success": 0,
            "duration_sum": 0.0,
            "duration_cnt": 0,
            "last_ts": None,
            "last_status": None,
        }
    )

    for job, status, started, finished in rows:
        data = agg[job]
        data["total"] += 1
        if status and status.lower() == "success":
            data["success"] += 1

        if started and finished:
            dur = (finished - started).total_seconds()
            data["duration_sum"] += dur
            data["duration_cnt"] += 1

        # Determine most recent run
        if finished:
            if data["last_ts"] is None or finished > data["last_ts"]:
                data["last_ts"] = finished
                data["last_status"] = status

    result = []
    for job, data in agg.items():
        total = data["total"]
        success = data["success"]
        failure = total - success
        success_rate = (success / total) * 100 if total else 0.0
        avg_duration = (
            data["duration_sum"] / data["duration_cnt"]
            if data["duration_cnt"]
            else None
        )
        result.append(
            JobAuditItem(
                name=job,
                total_runs=total,
                success_count=success,
                failure_count=failure,
                success_rate_pct=round(success_rate, 2),
                avg_duration_sec=round(avg_duration, 2) if avg_duration is not None else None,
                last_run_at=data["last_ts"],
                last_status=data["last_status"],
            )
        )

    return JobsAuditResponse(jobs=result)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.cadence_job_runs_audit.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables in the in‑memory DB
    Base.metadata.create_all(engine)

    # Seed data: 3 jobs, 5 runs each, mixed success/failure
    now = datetime.datetime.utcnow()
    job_names = ["job_alpha", "job_beta", "job_gamma"]
    statuses = ["success", "failure"]
    with SessionLocal() as db:
        for job in job_names:
            for i in range(5):
                started = now - datetime.timedelta(minutes=10 * (i + 1))
                finished = started + datetime.timedelta(minutes=5)
                status = statuses[i % 2]  # alternating success/failure
                run = CadenceJobRun(
                    job=job,
                    status=status,
                    started_at=started,
                    finished_at=finished,
                    detail={},
                    rows_affected=0,
                )
                db.add(run)
        db.commit()

    # Override the dependency to use the test session
    def get_test_session():
        with SessionLocal() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    resp = client.get("/api/cadence/jobs/audit")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    jobs = data.get("jobs", [])
    if len(jobs) < 3:
        print("FAIL: Expected at least 3 job entries", file=sys.stderr)
        sys.exit(1)

    if not any(job.get("success_count", 0) >= 1 for job in jobs):
        print("FAIL: No job with at least one success", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)