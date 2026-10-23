# services/staged/cadence_job_runs_health_api/logic.py
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select

from app.db import Base, get_session
from app.models import CadenceJobRun


class OverallStatus(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    stale = "stale"


class JobHealth(BaseModel):
    name: str = Field(..., alias="name")
    status: str = Field(..., alias="status")
    last_run_at: datetime = Field(..., alias="last_run_at")
    duration_seconds: Optional[float] = Field(None, alias="duration_seconds")
    rows_affected: Optional[int] = Field(None, alias="rows_affected")
    error_detail: Optional[dict] = Field(None, alias="error_detail")


class HealthSummary(BaseModel):
    total: int = Field(..., alias="total")
    success: int = Field(..., alias="success")
    failed: int = Field(..., alias="failed")
    running: int = Field(..., alias="running")
    error: int = Field(..., alias="error")


class HealthResponse(BaseModel):
    overall_status: OverallStatus = Field(..., alias="overall_status")
    summary: HealthSummary = Field(..., alias="summary")
    jobs: List[JobHealth] = Field(..., alias="jobs")


def _determine_overall_status(summary: HealthSummary) -> OverallStatus:
    if summary.total == 0:
        return OverallStatus.stale
    if summary.failed > 0 or summary.error > 0:
        return OverallStatus.degraded
    return OverallStatus.healthy


def get_cadence_job_runs_health(
    session=Depends(get_session),
) -> HealthResponse:
    """Return aggregated health information for Cadence job runs."""
    now = datetime.utcnow()
    since = now - timedelta(hours=24)

    stmt = (
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
            CadenceJobRun.detail,
        )
        .where(CadenceJobRun.started_at >= since)
        .order_by(CadenceJobRun.started_at.desc())
    )
    rows = session.execute(stmt).all()

    # Summary aggregation
    total = len(rows)
    success = sum(1 for r in rows if r.status == "success")
    failed = sum(1 for r in rows if r.status == "failed")
    running = sum(1 for r in rows if r.status == "running")
    error = sum(1 for r in rows if r.status == "error")

    summary = HealthSummary(
        total=total,
        success=success,
        failed=failed,
        running=running,
        error=error,
    )

    # Per‑job latest run
    latest_by_job = {}
    for r in rows:
        job_name = r.job
        if job_name not in latest_by_job:
            latest_by_job[job_name] = r

    jobs: List[JobHealth] = []
    for job_name, r in latest_by_job.items():
        duration = (
            (r.finished_at - r.started_at).total_seconds()
            if r.finished_at and r.started_at
            else None
        )
        jobs.append(
            JobHealth(
                name=job_name,
                status=r.status,
                last_run_at=r.started_at,
                duration_seconds=duration,
                rows_affected=r.rows_affected,
                error_detail=r.detail.get("error") if isinstance(r.detail, dict) else None,
            )
        )

    overall_status = _determine_overall_status(summary)

    return HealthResponse(
        overall_status=overall_status,
        summary=summary,
        jobs=jobs,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite DB that mirrors the real schema
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession()

    # Seed five distinct jobs with mixed statuses
    now = datetime.utcnow()
    seed_data = [
        CadenceJobRun(
            job="job_a",
            status="success",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1, minutes=30),
            rows_affected=10,
            detail={},
        ),
        CadenceJobRun(
            job="job_b",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2, minutes=15),
            rows_affected=5,
            detail={"error": {"msg": "boom"}},
        ),
        CadenceJobRun(
            job="job_c",
            status="running",
            started_at=now - timedelta(minutes=10),
            finished_at=None,
            rows_affected=None,
            detail={},
        ),
        CadenceJobRun(
            job="job_d",
            status="error",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=3, minutes=5),
            rows_affected=0,
            detail={"error": {"msg": "timeout"}},
        ),
        CadenceJobRun(
            job="job_e",
            status="success",
            started_at=now - timedelta(hours=25),  # outside 24h window
            finished_at=now - timedelta(hours=25, minutes=5),
            rows_affected=7,
            detail={},
        ),
    ]
    session.add_all(seed_data)
    session.commit()

    # Invoke the logic
    result = get_cadence_job_runs_health(session)

    # Basic assertions per acceptance criteria
    assert result.overall_status in OverallStatus.__members__.values()
    assert len(result.jobs) == 4  # job_e is older than 24h and excluded
    print("PASS")