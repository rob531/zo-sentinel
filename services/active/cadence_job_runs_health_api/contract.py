import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, sessionmaker

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


class OverallStatus(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    stale = "stale"


class JobHealth(BaseModel):
    name: str = Field(..., alias="name")
    status: str
    last_run_at: datetime
    duration_seconds: Optional[float] = None
    rows_affected: Optional[int] = None
    error_detail: Optional[Dict[str, Any]] = None


class Summary(BaseModel):
    total: int
    success: int
    failed: int
    running: int
    error: int


class HealthResponse(BaseModel):
    overall_status: OverallStatus
    summary: Summary
    jobs: List[JobHealth]


@router.get("/cadence/health", response_model=HealthResponse)
def get_cadence_health(session: Session = Depends(get_session)) -> HealthResponse:
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)

    runs_stmt = select(CadenceJobRun).where(CadenceJobRun.started_at >= day_ago)
    runs = session.execute(runs_stmt).scalars().all()

    if not runs:
        return HealthResponse(
            overall_status=OverallStatus.stale,
            summary=Summary(total=0, success=0, failed=0, running=0, error=0),
            jobs=[],
        )

    # Summary counts
    total = len(runs)
    success = sum(1 for r in runs if r.status == "success")
    failed = sum(1 for r in runs if r.status == "failed")
    running = sum(1 for r in runs if r.status == "running")
    error = sum(1 for r in runs if r.status == "error")

    # Determine overall status
    if failed or error:
        overall = OverallStatus.degraded
    else:
        overall = OverallStatus.healthy

    # Per‑job latest run
    latest_by_job: Dict[str, CadenceJobRun] = {}
    for run in sorted(runs, key=lambda r: r.started_at, reverse=True):
        if run.job not in latest_by_job:
            latest_by_job[run.job] = run

    jobs: List[JobHealth] = []
    for job_name, run in latest_by_job.items():
        duration = (
            (run.finished_at - run.started_at).total_seconds()
            if run.finished_at and run.started_at
            else None
        )
        error_detail = None
        if run.detail:
            try:
                error_detail = json.loads(run.detail)
            except Exception:
                error_detail = {"raw": run.detail}
        jobs.append(
            JobHealth(
                name=job_name,
                status=run.status,
                last_run_at=run.started_at,
                duration_seconds=duration,
                rows_affected=run.rows_affected,
                error_detail=error_detail,
            )
        )

    return HealthResponse(
        overall_status=overall,
        summary=Summary(
            total=total,
            success=success,
            failed=failed,
            running=running,
            error=error,
        ),
        jobs=jobs,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.cadence_job_runs_health_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    TestSession = sessionmaker(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        with TestSession() as sess:
            yield sess

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    now = datetime.utcnow()
    seed = [
        CadenceJobRun(
            job="job_a",
            status="success",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1, minutes=30),
            rows_affected=10,
            detail=None,
        ),
        CadenceJobRun(
            job="job_b",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2, minutes=15),
            rows_affected=5,
            detail='{"msg":"boom"}',
        ),
        CadenceJobRun(
            job="job_c",
            status="running",
            started_at=now - timedelta(minutes=10),
            finished_at=None,
            rows_affected=None,
            detail=None,
        ),
        CadenceJobRun(
            job="job_d",
            status="error",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=3, minutes=5),
            rows_affected=0,
            detail='{"error":"timeout"}',
        ),
        CadenceJobRun(
            job="job_e",
            status="success",
            started_at=now - timedelta(hours=20),
            finished_at=now - timedelta(hours=20, minutes=20),
            rows_affected=7,
            detail=None,
        ),
    ]

    with TestSession() as s:
        s.add_all(seed)
        s.commit()

    client = TestClient(app)
    resp = client.get("/api/cadence/health")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["overall_status"] in {"healthy", "degraded", "stale"}
    assert isinstance(data["jobs"], list)
    assert len(data["jobs"]) == 5
    print("PASS")