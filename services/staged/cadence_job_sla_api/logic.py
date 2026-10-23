import datetime
from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base  # get_session is the app's DB dependency
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DEFAULT_SLA_SECONDS = 300  # default SLA threshold (seconds)


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class JobSLA(BaseModel):
    name: str = Field(..., description="Job name")
    run_count: int = Field(..., description="Total number of runs")
    success_rate: float = Field(..., description="Successful runs / total runs")
    p50_sec: Optional[float] = Field(None, description="50th percentile duration (seconds)")
    p95_sec: Optional[float] = Field(None, description="95th percentile duration (seconds)")
    last_run: Optional[datetime.datetime] = Field(
        None, description="Timestamp of the most recent run"
    )
    last_success: Optional[datetime.datetime] = Field(
        None, description="Timestamp of the most recent successful run"
    )
    last_failure: Optional[datetime.datetime] = Field(
        None, description="Timestamp of the most recent failed run"
    )
    sla_breached: bool = Field(..., description="True if average duration exceeds SLA")


class JobsResponse(BaseModel):
    jobs: List[JobSLA]


# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------
def _percentile(sorted_vals: List[float], perc: float) -> Optional[float]:
    """Return the percentile value from a sorted list (0‑100)."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (perc / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


# ----------------------------------------------------------------------
# Core logic
# ----------------------------------------------------------------------
@router.get("/cadence/sla", response_model=JobsResponse)
def get_cadence_job_sla(
    db: Session = Depends(get_session),
    sla_seconds: int = DEFAULT_SLA_SECONDS,
):
    """
    Compute SLA metrics for each Cadence job.
    """
    runs = db.query(CadenceJobRun).all()
    if not runs:
        return JobsResponse(jobs=[])

    # Group runs by job name
    grouped = defaultdict(list)
    for run in runs:
        grouped[run.job].append(run)

    job_sla_list: List[JobSLA] = []

    for job_name, job_runs in grouped.items():
        durations: List[float] = []
        success_times: List[datetime.datetime] = []
        failure_times: List[datetime.datetime] = []
        all_finish_times: List[datetime.datetime] = []

        success_count = 0

        for run in job_runs:
            # Guard against missing timestamps
            if run.started_at and run.finished_at:
                dur = (run.finished_at - run.started_at).total_seconds()
                durations.append(dur)
            if run.finished_at:
                all_finish_times.append(run.finished_at)

            if run.status == "success":
                success_count += 1
                if run.finished_at:
                    success_times.append(run.finished_at)
            else:
                if run.finished_at:
                    failure_times.append(run.finished_at)

        run_count = len(job_runs)
        success_rate = success_count / run_count if run_count else 0.0

        durations_sorted = sorted(durations)
        p50 = _percentile(durations_sorted, 50)
        p95 = _percentile(durations_sorted, 95)

        last_run = max(all_finish_times) if all_finish_times else None
        last_success = max(success_times) if success_times else None
        last_failure = max(failure_times) if failure_times else None

        avg_duration = sum(durations) / len(durations) if durations else 0.0
        sla_breached = avg_duration > sla_seconds

        job_sla = JobSLA(
            name=job_name,
            run_count=run_count,
            success_rate=success_rate,
            p50_sec=p50,
            p95_sec=p95,
            last_run=last_run,
            last_success=last_success,
            last_failure=last_failure,
            sla_breached=sla_breached,
        )
        job_sla_list.append(job_sla)

    return JobsResponse(jobs=job_sla_list)


# ----------------------------------------------------------------------
# Self‑test (executed when running this file directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------
    # In‑memory SQLite setup (overrides the real DB dependency)
    # ------------------------------------------------------------------
    TEST_DATABASE_URL = "sqlite://"
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def get_test_session() -> Session:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Seed test data
    # ------------------------------------------------------------------
    now = datetime.datetime.utcnow()
    test_runs = [
        # job1: fast, all success
        CadenceJobRun(
            job="job1",
            status="success",
            started_at=now - datetime.timedelta(seconds=10),
            finished_at=now - datetime.timedelta(seconds=5),
            detail="run1",
            rows_affected=0,
        ),
        CadenceJobRun(
            job="job1",
            status="success",
            started_at=now - datetime.timedelta(seconds=20),
            finished_at=now - datetime.timedelta(seconds=15),
            detail="run2",
            rows_affected=0,
        ),
        # job2: mixed, long duration (will breach SLA)
        CadenceJobRun(
            job="job2",
            status="success",
            started_at=now - datetime.timedelta(seconds=600),
            finished_at=now - datetime.timedelta(seconds=200),
            detail="run1",
            rows_affected=0,
        ),
        CadenceJobRun(
            job="job2",
            status="failure",
            started_at=now - datetime.timedelta(seconds=800),
            finished_at=now - datetime.timedelta(seconds=100),
            detail="run2",
            rows_affected=0,
        ),
        # job3: short, mixed success/failure, does not breach SLA
        CadenceJobRun(
            job="job3",
            status="failure",
            started_at=now - datetime.timedelta(seconds=30),
            finished_at=now - datetime.timedelta(seconds=20),
            detail="run1",
            rows_affected=0,
        ),
        CadenceJobRun(
            job="job3",
            status="success",
            started_at=now - datetime.timedelta(seconds=25),
            finished_at=now - datetime.timedelta(seconds=22),
            detail="run2",
            rows_affected=0,
        ),
    ]

    with TestingSessionLocal() as db:
        db.add_all(test_runs)
        db.commit()

    # ------------------------------------------------------------------
    # Build FastAPI app with overridden dependency
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------
    # Execute request and validate response
    # ------------------------------------------------------------------
    response = client.get("/api/cadence/sla")
    try:
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        data = response.json()
        assert "jobs" in data, "Missing 'jobs' key"
        assert len(data["jobs"]) == 3, f"Expected 3 jobs, got {len(data['jobs'])}"

        # Find job2 and ensure SLA breach flag is True
        job2 = next(j for j in data["jobs"] if j["name"] == "job2")
        assert job2["sla_breached"] is True, "job2 should be SLA breached"

        print("PASS")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)