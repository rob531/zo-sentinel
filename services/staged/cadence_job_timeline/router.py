"""HTTP surface for the cadence job timeline service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import *  # noqa: F401,F403

router = APIRouter(prefix="/api", tags=["cadence-job-timeline"])


class TimelineRun(BaseModel):
    job: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    rows_affected: int | None
    detail: Any


class TimelineJobSummary(BaseModel):
    job: str
    run_count: int
    success_count: int
    failure_count: int
    last_started_at: datetime
    last_status: str
    avg_duration_seconds: float | None
    total_rows_affected: int


class CadenceJobTimelineResponse(BaseModel):
    jobs: list[TimelineJobSummary]
    runs: list[TimelineRun]
    total_runs: int
    generated_at: datetime


def _duration_seconds(run: CadenceJobRun) -> float | None:
    if run.finished_at is None or run.started_at is None:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def _is_success(status: str) -> bool:
    return status.strip().lower() in {"success", "succeeded", "complete", "completed", "ok"}


def _is_failure(status: str) -> bool:
    return status.strip().lower() in {"failure", "failed", "error", "errored"}


def _timeline_response(
    db: Session, job: str | None, limit: int
) -> CadenceJobTimelineResponse:
    limit = max(1, min(limit, 500))
    statement = select(CadenceJobRun)
    if job:
        statement = statement.where(CadenceJobRun.job == job)
    statement = statement.order_by(
        CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc()
    )
    all_runs = list(db.execute(statement).scalars().all())

    summaries: list[TimelineJobSummary] = []
    for name in sorted({run.job for run in all_runs}):
        job_runs = [run for run in all_runs if run.job == name]
        durations = [
            duration
            for run in job_runs
            if (duration := _duration_seconds(run)) is not None
        ]
        last_run = max(job_runs, key=lambda run: run.started_at)
        summaries.append(
            TimelineJobSummary(
                job=name,
                run_count=len(job_runs),
                success_count=sum(_is_success(run.status) for run in job_runs),
                failure_count=sum(_is_failure(run.status) for run in job_runs),
                last_started_at=last_run.started_at,
                last_status=last_run.status,
                avg_duration_seconds=(sum(durations) / len(durations)) if durations else None,
                total_rows_affected=sum(run.rows_affected or 0 for run in job_runs),
            )
        )

    selected_runs = all_runs[:limit]
    runs = [
        TimelineRun(
            job=run.job,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_seconds=_duration_seconds(run),
            rows_affected=run.rows_affected,
            detail=run.detail,
        )
        for run in selected_runs
    ]

    return CadenceJobTimelineResponse(
        jobs=summaries,
        runs=runs,
        total_runs=len(all_runs),
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/cadence/jobs/timeline", response_model=CadenceJobTimelineResponse
)
def get_cadence_job_timeline(
    job: str | None = Query(default=None),
    limit: int = Query(default=100),
    db: Session = Depends(get_session),
) -> CadenceJobTimelineResponse:
    return _timeline_response(db, job, limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2024, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="scanner",
                    status="success",
                    started_at=started,
                    finished_at=finished,
                    rows_affected=10,
                    detail={"message": "ok"},
                ),
                CadenceJobRun(
                    id=2,
                    job="scanner",
                    status="failure",
                    started_at=datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
                    finished_at=datetime(2024, 1, 1, 13, 0, 2, tzinfo=timezone.utc),
                    rows_affected=0,
                    detail={"message": "failed"},
                ),
                CadenceJobRun(
                    id=3,
                    job="enricher",
                    status="success",
                    started_at=datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc),
                    finished_at=datetime(2024, 1, 1, 14, 0, 3, tzinfo=timezone.utc),
                    rows_affected=4,
                    detail={"message": "ok"},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()
    with TestClient(test_app) as client:
        response = client.get("/api/cadence/jobs/timeline", params={"limit": 10})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        scanner = next(item for item in payload["jobs"] if item["job"] == "scanner")
        assert scanner["success_count"] == 1
        assert scanner["failure_count"] == 1
        assert any(
            run["job"] == "scanner"
            and run["status"] == "success"
            and isinstance(run["duration_seconds"], (int, float))
            and run["duration_seconds"] > 0
            for run in payload["runs"]
        )

    print("PASS")