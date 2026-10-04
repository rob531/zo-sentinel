# deps: fastapi, pydantic, sqlalchemy
"""cadence_monitor service.

Provides monitoring endpoints for cadence job runs:
  GET /api/cadence/jobs           -- list job runs with optional filters
  GET /api/cadence/jobs/{job}     -- detail for a specific job
  GET /api/cadence/summary        -- aggregate status counts

APP table (cadence_job_runs): via get_session + SQLAlchemy.
Public endpoint (auth=public per the directive).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api/cadence", tags=["cadence_monitor"])


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


class JobRunListResponse(BaseModel):
    job_runs: list[JobRunOut]
    total: int
    page: int
    page_size: int


class JobRunDetailResponse(BaseModel):
    job_run: JobRunOut


class StatusSummary(BaseModel):
    status: str
    count: int


class JobSummary(BaseModel):
    job: str
    run_count: int
    last_status: Optional[str] = None
    last_run_at: Optional[datetime] = None


class CadenceSummaryResponse(BaseModel):
    total_runs: int
    by_status: list[StatusSummary]
    recent_jobs: list[JobSummary]


# --------------------------------------------------------------------------- #
# GET /api/cadence/jobs
# --------------------------------------------------------------------------- #


@router.get("/jobs", response_model=JobRunListResponse)
def list_job_runs(
    job: Optional[str] = Query(None, description="Filter by job name"),
    status: Optional[str] = Query(None, description="Filter by status (e.g. completed, failed)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> JobRunListResponse:
    """Return a paginated list of cadence job runs, optionally filtered."""
    stmt = select(CadenceJobRun)

    if job:
        stmt = stmt.where(CadenceJobRun.job == job)
    if status:
        stmt = stmt.where(CadenceJobRun.status == status)

    # total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.execute(count_stmt).scalar() or 0

    # paginated results
    offset = (page - 1) * page_size
    stmt = stmt.order_by(CadenceJobRun.started_at.desc()).offset(offset).limit(page_size)
    rows = session.execute(stmt).scalars().all()

    job_runs = [JobRunOut.model_validate(r) for r in rows]
    return JobRunListResponse(job_runs=job_runs, total=total, page=page, page_size=page_size)


# --------------------------------------------------------------------------- #
# GET /api/cadence/jobs/{job}
# --------------------------------------------------------------------------- #


@router.get("/jobs/{job}", response_model=JobRunDetailResponse)
def get_job_run(job: str, session: Session = Depends(get_session)) -> JobRunDetailResponse:
    """Return the most recent run for a named job."""
    stmt = (
        select(CadenceJobRun)
        .where(CadenceJobRun.job == job)
        .order_by(CadenceJobRun.started_at.desc())
        .limit(1)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No run found for job '{job}'")
    return JobRunDetailResponse(job_run=JobRunOut.model_validate(row))


# --------------------------------------------------------------------------- #
# GET /api/cadence/summary
# --------------------------------------------------------------------------- #


@router.get("/summary", response_model=CadenceSummaryResponse)
def cadence_summary(session: Session = Depends(get_session)) -> CadenceSummaryResponse:
    """Return aggregate cadence job run statistics."""
    # total runs
    total = session.execute(select(func.count()).select_from(CadenceJobRun)).scalar() or 0

    # by-status counts
    status_rows = (
        session.execute(
            select(CadenceJobRun.status, func.count())
            .group_by(CadenceJobRun.status)
            .order_by(func.count().desc())
        )
        .all()
    )
    by_status = [StatusSummary(status=s, count=c) for s, c in status_rows]

    # recent per-job summary (latest run per job)
    recent_stmt = (
        select(
            CadenceJobRun.job,
            func.count().label("run_count"),
            func.max(CadenceJobRun.started_at).label("last_run_at"),
        )
        .group_by(CadenceJobRun.job)
        .order_by(func.max(CadenceJobRun.started_at).desc())
        .limit(20)
    )
    recent_rows = session.execute(recent_stmt).all()

    # fetch last status per job
    job_summaries = []
    for row in recent_rows:
        last_status_row = (
            session.execute(
                select(CadenceJobRun.status)
                .where(CadenceJobRun.job == row.job)
                .order_by(CadenceJobRun.started_at.desc())
                .limit(1)
            )
            .scalar_one_or_none()
        )
        job_summaries.append(
            JobSummary(
                job=row.job,
                run_count=row.run_count,
                last_status=last_status_row,
                last_run_at=row.last_run_at,
            )
        )

    return CadenceSummaryResponse(total_runs=total, by_status=by_status, recent_jobs=job_summaries)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime, timedelta

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE cadence_job_runs ("
                "id INTEGER PRIMARY KEY, job TEXT NOT NULL, status TEXT NOT NULL, "
                "started_at TEXT, finished_at TEXT, rows_affected INTEGER, detail TEXT)"
            )
        )
        conn.commit()

    now = datetime.utcnow()
    test_data = [
        ("scan_servers", "completed", now - timedelta(hours=2), now - timedelta(hours=1), 50, "ok"),
        ("scan_servers", "failed", now - timedelta(hours=1), now - timedelta(minutes=50), 0, "error"),
        ("ingest_vuln", "completed", now - timedelta(hours=3), now - timedelta(hours=2), 120, "ok"),
        ("score_batch", "running", now - timedelta(minutes=5), None, None, "in progress"),
    ]

    with engine.begin() as conn:
        for job, status, started, finished, rows, detail in test_data:
            conn.execute(
                text(
                    "INSERT INTO cadence_job_runs "
                    "(job, status, started_at, finished_at, rows_affected, detail) "
                    "VALUES (:job, :status, :started_at, :finished_at, :rows, :detail)"
                ),
                {
                    "job": job,
                    "status": status,
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat() if finished else None,
                    "rows": rows,
                    "detail": detail,
                },
            )

    the_app = FastAPI()
    the_app.include_router(router)
    the_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(the_app, raise_server_exceptions=False)

    # Test list endpoint
    resp = client.get("/api/cadence/jobs")
    assert resp.status_code == 200, f"list failed: {resp.text}"
    data = resp.json()
    assert "job_runs" in data, f"missing job_runs: {data}"
    assert len(data["job_runs"]) == 4, f"expected 4 rows, got {len(data['job_runs'])}"
    assert data["total"] == 4

    # Filter by job
    resp2 = client.get("/api/cadence/jobs?job=scan_servers")
    assert resp2.status_code == 200
    jobs = resp2.json()["job_runs"]
    assert all(r["job"] == "scan_servers" for r in jobs), f"job filter failed: {jobs}"

    # Filter by status
    resp3 = client.get("/api/cadence/jobs?status=failed")
    assert resp3.status_code == 200
    failed = resp3.json()["job_runs"]
    assert all(r["status"] == "failed" for r in failed), f"status filter failed: {failed}"

    # Test detail endpoint
    resp4 = client.get("/api/cadence/jobs/scan_servers")
    assert resp4.status_code == 200
    detail = resp4.json()["job_run"]
    assert detail["job"] == "scan_servers"

    # Test summary
    resp5 = client.get("/api/cadence/summary")
    assert resp5.status_code == 200, f"summary failed: {resp5.text}"
    summary = resp5.json()
    assert summary["total_runs"] == 4
    status_counts = {s["status"]: s["count"] for s in summary["by_status"]}
    assert status_counts.get("completed", 0) >= 2
    assert status_counts.get("failed", 0) >= 1
    assert status_counts.get("running", 0) >= 1

    # 404 on unknown job
    resp6 = client.get("/api/cadence/jobs/nonexistent_job")
    assert resp6.status_code == 404, f"expected 404 for unknown job: {resp6.status_code}"

    print("PASS")
