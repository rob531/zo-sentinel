"""
services.staged.cadence_job_sla_report.contract
"""

from __future__ import annotations

import datetime
import statistics
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import CadenceJobRun  # type: ignore[attr-defined]

router = APIRouter(prefix="/api")


class CadenceJobSLA(BaseModel):
    job_name: str = Field(..., alias="job_name")
    total_runs: int
    success_count: int
    failure_count: int
    sla_pass_rate: float
    median_duration_seconds: Optional[float]
    last_run_at: Optional[datetime.datetime]


class CadenceJobSLAResponse(BaseModel):
    days: int
    generated_at: datetime.datetime
    jobs: List[CadenceJobSLA]


@router.get(
    "/cadence/jobs/sla",
    response_model=CadenceJobSLAResponse,
    summary="SLA report for Cadence jobs",
)
def get_cadence_job_sla(
    days: int = Query(..., ge=1, description="Number of days to look back"),
    session: Session = Depends(get_session),
) -> CadenceJobSLAResponse:
    """Return SLA statistics for Cadence jobs over the past *days*."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    stmt = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    rows = session.execute(stmt).scalars().all()

    jobs_dict: dict[str, List[CadenceJobRun]] = {}
    for row in rows:
        jobs_dict.setdefault(row.job, []).append(row)

    job_slas: List[CadenceJobSLA] = []
    for job_name, runs in jobs_dict.items():
        total = len(runs)
        success = sum(1 for r in runs if r.status == "success")
        failure = sum(1 for r in runs if r.status == "failure")
        sla_pass_rate = success / total if total else 0.0

        durations = [
            (r.finished_at - r.started_at).total_seconds()
            for r in runs
            if r.finished_at and r.started_at
        ]
        median_duration = (
            statistics.median(durations) if durations else None
        )

        last_run_at = max((r.started_at for r in runs), default=None)

        job_slas.append(
            CadenceJobSLA(
                job_name=job_name,
                total_runs=total,
                success_count=success,
                failure_count=failure,
                sla_pass_rate=sla_pass_rate,
                median_duration_seconds=median_duration,
                last_run_at=last_run_at,
            )
        )

    response = CadenceJobSLAResponse(
        days=days,
        generated_at=datetime.datetime.utcnow(),
        jobs=sorted(job_slas, key=lambda x: x.job_name),
    )
    return response


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.cadence_job_sla_report.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a minimal FastAPI app for the self‑test
    app = FastAPI()
    app.include_router(router)

    # In‑memory SQLite engine (StaticPool for thread‑safety)
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)

    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    async def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Override the real DB dependency with the in‑memory one
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSessionLocal() as db:
        now = datetime.datetime.utcnow()
        # job_a: 3 successful runs
        for i in range(3):
            db.add(
                CadenceJobRun(
                    job="job_a",
                    status="success",
                    started_at=now - datetime.timedelta(hours=2 + i),
                    finished_at=now - datetime.timedelta(hours=1 + i),
                    rows_affected=10,
                )
            )
        # job_b: 2 failed runs
        for i in range(2):
            db.add(
                CadenceJobRun(
                    job="job_b",
                    status="failure",
                    started_at=now - datetime.timedelta(hours=5 + i),
                    finished_at=now - datetime.timedelta(hours=4 + i),
                    rows_affected=5,
                )
            )
        # job_c: 1 successful run
        db.add(
            CadenceJobRun(
                job="job_c",
                status="success",
                started_at=now - datetime.timedelta(hours=8),
                finished_at=now - datetime.timedelta(hours=7, minutes=30),
                rows_affected=7,
            )
        )
        db.commit()

    client = TestClient(app)

    resp = client.get("/api/cadence/jobs/sla?days=7")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["days"] == 7
    jobs = {j["job_name"]: j for j in data["jobs"]}

    assert len(jobs) == 3, f"Expected 3 jobs, got {len(jobs)}"
    assert jobs["job_a"]["sla_pass_rate"] == 1.0, "job_a SLA should be 1.0"
    assert jobs["job_b"]["sla_pass_rate"] == 0.0, "job_b SLA should be 0.0"
    assert jobs["job_c"]["median_duration_seconds"] is not None
    assert jobs["job_c"]["median_duration_seconds"] > 0, "job_c median duration should be >0"

    print("PASS")