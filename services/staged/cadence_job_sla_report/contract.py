"""
services/staged/cadence_job_sla_report/contract.py

FastAPI contract for the cadence job SLA report service.
Mirrors the exemplar contract while using the real application models
and database session.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

# Import the real application session dependency and models
from app.db import get_session  # FastAPI dependency that yields a Session
from app.models import CadenceJobRun  # The ORM model for cadence job runs

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class SLAJobDetail(BaseModel):
    job_name: str = Field(..., description="Name of the cadence job")
    success_rate_pct: float = Field(..., description="Success rate as a percentage")
    avg_duration_s: Optional[float] = Field(
        None, description="Average duration of successful runs in seconds"
    )
    p95_duration_s: Optional[float] = Field(
        None, description="95th percentile duration of successful runs in seconds"
    )
    total_runs: int = Field(..., description="Total number of runs in the window")
    stale_runs: int = Field(..., description="Number of stale runs")
    sla_tier: str = Field(..., description="SLA tier classification (GREEN/AMBER/RED)")
    detail: Optional[Dict[str, Any]] = Field(
        None, description="Arbitrary JSON detail from the latest run"
    )


class SLAResponse(BaseModel):
    window_hours: int = Field(..., description="Window size in hours")
    jobs: List[SLAJobDetail] = Field(..., description="Per‑job SLA details")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _compute_percentile(data: List[float], percentile: float) -> float:
    """Return the given percentile (0‑100) of the data list."""
    if not data:
        return 0.0
    data_sorted = sorted(data)
    k = (len(data_sorted) - 1) * (percentile / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(data_sorted):
        return float(data_sorted[-1])
    d0 = data_sorted[f] * (c - k)
    d1 = data_sorted[c] * (k - f)
    return float(d0 + d1)


def _classify_sla_tier(success_rate: float, stale_runs: int) -> str:
    """Classify SLA tier based on success rate and staleness."""
    if stale_runs == 0 and success_rate >= 95.0:
        return "GREEN"
    if success_rate >= 80.0:
        return "AMBER"
    return "RED"


# ---------------------------------------------------------------------------
# Endpoint implementation
# ---------------------------------------------------------------------------


@router.get(
    "/api/cadence/sla",
    response_model=SLAResponse,
    summary="Get SLA report for cadence jobs",
)
def get_sla_report(
    window_hours: int = Query(
        24,
        ge=1,
        description="Number of hours in the past to consider for the SLA window",
    ),
    session: Session = Depends(get_session),
) -> SLAResponse:
    """
    Compute SLA metrics for each cadence job over the past *window_hours*.
    """
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)

    # Pull all runs in the window
    runs = (
        session.query(CadenceJobRun)
        .filter(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.desc())
        .all()
    )

    if not runs:
        raise HTTPException(status_code=404, detail="No cadence job runs found")

    # Organise runs by job name
    jobs: Dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        jobs.setdefault(run.job, []).append(run)

    job_details: List[SLAJobDetail] = []

    for job_name, job_runs in jobs.items():
        total_runs = len(job_runs)
        success_runs = [r for r in job_runs if r.status.lower() == "success"]
        success_rate = (len(success_runs) / total_runs) * 100.0

        # Duration calculations (only for successful runs with a finished_at)
        durations = []
        for r in success_runs:
            if r.finished_at:
                delta = r.finished_at - r.started_at
                durations.append(delta.total_seconds())

        avg_duration = (
            statistics.mean(durations) if durations else None
        )
        p95_duration = (
            _compute_percentile(durations, 95) if durations else None
        )

        # Stale runs: finished_at is NULL or finished_at older than 2× expected duration
        stale_runs = 0
        for r in job_runs:
            if r.finished_at is None:
                stale_runs += 1
            elif avg_duration is not None:
                # If the run took more than twice the average duration, consider stale
                delta = r.finished_at - r.started_at
                if delta.total_seconds() > 2 * avg_duration:
                    stale_runs += 1

        sla_tier = _classify_sla_tier(success_rate, stale_runs)

        # Use the most recent run's detail as representative
        latest_detail_raw = job_runs[0].detail if job_runs else None
        latest_detail = (
            json.loads(latest_detail_raw) if isinstance(latest_detail_raw, str) else latest_detail_raw
        )

        job_details.append(
            SLAJobDetail(
                job_name=job_name,
                success_rate_pct=round(success_rate, 2),
                avg_duration_s=round(avg_duration, 2) if avg_duration is not None else None,
                p95_duration_s=round(p95_duration, 2) if p95_duration is not None else None,
                total_runs=total_runs,
                stale_runs=stale_runs,
                sla_tier=sla_tier,
                detail=latest_detail,
            )
        )

    return SLAResponse(window_hours=window_hours, jobs=job_details)


# ---------------------------------------------------------------------------
# FastAPI app definition
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router)


# ---------------------------------------------------------------------------
# Self‑test (executed when running the module directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    # -----------------------------------------------------------------------
    # Create an in‑memory SQLite database that mimics the real tables
    # -----------------------------------------------------------------------
    engine: Engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import the declarative Base from the app so we can create tables
    from app.db import Base  # noqa: E402

    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    test_session = SessionLocal()

    # -----------------------------------------------------------------------
    # Seed the database with representative data
    # -----------------------------------------------------------------------
    now = datetime.utcnow()
    sample_data = [
        # Job A – mostly successful, no stale runs
        CadenceJobRun(
            job="job_a",
            status="success",
            started_at=now - timedelta(hours=1, minutes=10),
            finished_at=now - timedelta(hours=1),
            detail=json.dumps({"info": "run A1"}),
        ),
        CadenceJobRun(
            job="job_a",
            status="success",
            started_at=now - timedelta(hours=5, minutes=20),
            finished_at=now - timedelta(hours=5, minutes=10),
            detail=json.dumps({"info": "run A2"}),
        ),
        CadenceJobRun(
            job="job_a",
            status="failure",
            started_at=now - timedelta(hours=8),
            finished_at=now - timedelta(hours=7, minutes=55),
            detail=json.dumps({"info": "run A3"}),
        ),
        # Job B – mixed success, one stale (NULL finished_at)
        CadenceJobRun(
            job="job_b",
            status="success",
            started_at=now - timedelta(hours=2, minutes=30),
            finished_at=now - timedelta(hours=2, minutes=20),
            detail=json.dumps({"info": "run B1"}),
        ),
        CadenceJobRun(
            job="job_b",
            status="failure",
            started_at=now - timedelta(hours=12),
            finished_at=now - timedelta(hours=11, minutes=50),
            detail=json.dumps({"info": "run B2"}),
        ),
        CadenceJobRun(
            job="job_b",
            status="success",
            started_at=now - timedelta(hours=20),
            finished_at=None,  # stale run
            detail=json.dumps({"info": "run B3"}),
        ),
        # Job C – low success rate, all runs fast
        CadenceJobRun(
            job="job_c",
            status="failure",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=2, minutes=55),
            detail=json.dumps({"info": "run C1"}),
        ),
        CadenceJobRun(
            job="job_c",
            status="failure",
            started_at=now - timedelta(hours=6),
            finished_at=now - timedelta(hours=5, minutes=55),
            detail=json.dumps({"info": "run C2"}),
        ),
        CadenceJobRun(
            job="job_c",
            status="success",
            started_at=now - timedelta(hours=9),
            finished_at=now - timedelta(hours=8, minutes=55),
            detail=json.dumps({"info": "run C3"}),
        ),
        # Job D – single successful run
        CadenceJobRun(
            job="job_d",
            status="success",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3, minutes=50),
            detail=json.dumps({"info": "run D1"}),
        ),
        # Job E – all stale runs
        CadenceJobRun(
            job="job_e",
            status="success",
            started_at=now - timedelta(hours=22),
            finished_at=None,
            detail=json.dumps({"info": "run E1"}),
        ),
    ]

    test_session.add_all(sample_data)
    test_session.commit()

    # -----------------------------------------------------------------------
    # Override the FastAPI dependency to use our test session
    # -----------------------------------------------------------------------
    def get_test_session() -> Session:
        return test_session

    app.dependency_overrides[get_session] = get_test_session

    # -----------------------------------------------------------------------
    # Execute the test client request
    # -----------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/cadence/sla?hours=24")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert "jobs" in payload, "Response missing 'jobs' key"
    assert isinstance(payload["jobs"], list), "'jobs' is not a list"

    # Verify that at least one job is classified as GREEN
    green_jobs = [j for j in payload["jobs"] if j["sla_tier"] == "GREEN"]
    assert green_jobs, "No GREEN tier jobs found in the response"

    # Verify that p95_duration_s is numeric where present
    for job in payload["jobs"]:
        if job["p95_duration_s"] is not None:
            assert isinstance(job["p95_duration_s"], (int, float)), "p95_duration_s not numeric"

    print("PASS")