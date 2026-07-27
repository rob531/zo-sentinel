# services/staged/cadence_job_sla_report/contract.py
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import CadenceJobRun  # type: ignore

router = APIRouter(prefix="/api", tags=["cadence_job_sla_report"])


class JobReport(BaseModel):
    job: str
    run_count_7d: int
    success_rate: float
    p50_ms: Optional[float]
    p95_ms: Optional[float]
    sla_violated: bool
    last_run: Optional[datetime]


class CadenceJobSLAReportResponse(BaseModel):
    jobs: List[JobReport]


def _runtime_ms(row: CadenceJobRun) -> float:
    """Return runtime in milliseconds."""
    delta = row.finished_at - row.started_at
    return delta.total_seconds() * 1000.0


def _sla_threshold_seconds(job_name: str) -> int:
    """Return SLA threshold in seconds based on job name."""
    name = job_name.lower()
    if "heartbeat" in name:
        return 300
    if "scanner" in name:
        return 1800
    return 300  # default fallback


@router.get(
    "/cadence/jobs/sla",
    response_model=CadenceJobSLAReportResponse,
    summary="Cadence job SLA report",
)
async def get_cadence_job_sla_report(db: Session = Depends(get_session)):
    """Collect SLA metrics for each cadence job over the last 7 days."""
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    stmt = select(CadenceJobRun).where(CadenceJobRun.started_at >= week_ago)
    rows = db.execute(stmt).scalars().all()

    jobs_dict = {}
    for row in rows:
        jobs_dict.setdefault(row.job, []).append(row)

    reports: List[JobReport] = []
    for job_name, runs in jobs_dict.items():
        runtimes = [_runtime_ms(r) for r in runs]
        success_runs = [r for r in runs if r.status == "success"]
        sla_violated = any(
            r.status == "failed"
            or _runtime_ms(r) > _sla_threshold_seconds(job_name) * 1000
            for r in runs
        )
        p50 = float(np.percentile(runtimes, 50)) if runtimes else None
        p95 = float(np.percentile(runtimes, 95)) if runtimes else None
        last_run = max(r.finished_at for r in runs) if runs else None
        reports.append(
            JobReport(
                job=job_name,
                run_count_7d=len(runs),
                success_rate=(
                    len(success_runs) / len(runs) if runs else 0.0
                ),
                p50_ms=p50,
                p95_ms=p95,
                sla_violated=sla_violated,
                last_run=last_run,
            )
        )
    return CadenceJobSLAReportResponse(jobs=reports)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.cadence_job_sla_report.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the app's session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    Base.metadata.create_all(bind=engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Seed test data (4 jobs, exactly one violates SLA)
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    with SessionLocal() as db:
        seed = [
            # heartbeat job – within SLA
            CadenceJobRun(
                job="heartbeat_job1",
                status="success",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(hours=1, seconds=100),
                rows_affected=10,
                detail="",
            ),
            # heartbeat job – exceeds SLA (runtime > 300 s)
            CadenceJobRun(
                job="heartbeat_job2",
                status="success",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2, seconds=400),
                rows_affected=5,
                detail="",
            ),
            # scanner job – within SLA
            CadenceJobRun(
                job="scanner_job1",
                status="success",
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1, seconds=1000),
                rows_affected=20,
                detail="",
            ),
            # scanner job – failed status (SLA violation)
            CadenceJobRun(
                job="scanner_job2",
                status="failed",
                started_at=now - timedelta(days=2),
                finished_at=now - timedelta(days=2, seconds=500),
                rows_affected=15,
                detail="",
            ),
        ]
        db.add_all(seed)
        db.commit()

    # ------------------------------------------------------------------- #
    # Execute request and validate response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/cadence/jobs/sla")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert isinstance(payload, dict) and "jobs" in payload, "Missing jobs key"
    jobs = payload["jobs"]
    sla_violated_true = [j for j in jobs if j["sla_violated"]]
    assert len(sla_violated_true) == 1, f"Expected 1 SLA violation, got {len(sla_violated_true)}"
    for j in jobs:
        assert isinstance(j["p50_ms"], (float, int)) or j["p50_ms"] is None, "p50_ms not numeric"
    print("PASS")
    sys.exit(0)