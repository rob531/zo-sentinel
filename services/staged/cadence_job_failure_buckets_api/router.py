from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import bucket_status, summarise_job


router = APIRouter(prefix="/api", tags=["cadence-job-failure-buckets"])


class JobFailureBucket(BaseModel):
    job: str
    runs: int
    ok: int
    failed: int
    success_rate: float
    avg_duration_seconds: float
    last_run_at: Optional[str]
    last_status: Optional[str]


class FailureBucketsResponse(BaseModel):
    jobs: list[JobFailureBucket]
    total_runs: int
    window_days: int
    worst_job: Optional[str]
    computed_at: str


def _as_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _run_dict(run: CadenceJobRun) -> dict[str, Any]:
    return {
        "id": getattr(run, "id", None),
        "job": getattr(run, "job", None) or "unknown",
        "status": getattr(run, "status", None) or "unknown",
        "started_at": getattr(run, "started_at", None),
        "finished_at": getattr(run, "finished_at", None),
        "rows_affected": getattr(run, "rows_affected", None),
        "detail": getattr(run, "detail", None),
    }


def _normalise_summary(job: str, runs: list[dict[str, Any]], summary: dict[str, Any]) -> JobFailureBucket:
    summary = dict(summary or {})
    summary.setdefault("job", job)
    summary.setdefault("runs", len(runs))
    summary.setdefault("ok", 0)
    summary.setdefault("failed", 0)

    try:
        success_rate = float(summary.get("success_rate", 0.0))
    except (TypeError, ValueError):
        success_rate = 0.0
    success_rate = round(max(0.0, min(1.0, success_rate)), 3)

    try:
        average_duration = float(summary.get("avg_duration_seconds", 0.0))
    except (TypeError, ValueError):
        average_duration = 0.0
    average_duration = max(0.0, average_duration)

    last_status = summary.get("last_status")
    if last_status is None:
        last_status = runs[-1].get("status") if runs else None
    if last_status is not None:
        last_status = bucket_status(str(last_status))

    return JobFailureBucket(
        job=str(summary.get("job") or job),
        runs=max(0, int(summary.get("runs", len(runs)))),
        ok=max(0, int(summary.get("ok", 0))),
        failed=max(0, int(summary.get("failed", 0))),
        success_rate=success_rate,
        avg_duration_seconds=average_duration,
        last_run_at=_as_iso(summary.get("last_run_at")),
        last_status=last_status,
    )


@router.get(
    "/cadence/jobs/failure-buckets",
    response_model=FailureBucketsResponse,
)
def get_cadence_job_failure_buckets(
    session: Session = Depends(get_session),
) -> FailureBucketsResponse:
    window_days = 7
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    result = session.execute(
        select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    )
    runs = [_run_dict(run) for run in result.scalars().all()]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        job = str(run.get("job") or "unknown")
        grouped.setdefault(job, []).append(run)

    jobs = [
        _normalise_summary(job, job_runs, summarise_job(job_runs))
        for job, job_runs in grouped.items()
    ]
    jobs.sort(key=lambda item: item.job)

    if jobs:
        worst_job = min(
            jobs,
            key=lambda item: (item.success_rate, -item.failed, item.job),
        ).job
    else:
        worst_job = None

    return FailureBucketsResponse(
        jobs=jobs,
        total_runs=len(runs),
        window_days=window_days,
        worst_job=worst_job,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow() - timedelta(days=1)
    with SessionLocal() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="risk_ranker",
                    status="ok",
                    started_at=now,
                    finished_at=now + timedelta(seconds=10),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    job="risk_ranker",
                    status="ok",
                    started_at=now,
                    finished_at=now + timedelta(seconds=20),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    job="risk_ranker",
                    status="ok",
                    started_at=now,
                    finished_at=now - timedelta(seconds=5),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    job="risk_ranker",
                    status="error",
                    started_at=now,
                    finished_at=now + timedelta(seconds=5),
                    rows_affected=0,
                    detail="failure",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/cadence/jobs/failure-buckets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_runs"] == 4
    assert payload["jobs"][0]["runs"] == 4
    assert payload["jobs"][0]["failed"] == 1
    assert payload["jobs"][0]["success_rate"] == 0.75
    assert payload["jobs"][0]["avg_duration_seconds"] >= 0
    print("PASS")