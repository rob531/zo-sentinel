from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


class JobThroughput(BaseModel):
    job: str
    runs: int
    ok: int
    failed: int
    avg_seconds: Optional[float]
    last_status: str
    last_started_at: datetime


class ThroughputResponse(BaseModel):
    window_hours: int
    jobs: list[JobThroughput]


def _window_start(window_hours: int) -> datetime:
    return datetime.utcnow() - timedelta(hours=window_hours)


def _duration_seconds(run: CadenceJobRun) -> Optional[float]:
    if run.started_at is None or run.finished_at is None:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def cadence_window_throughput(
    window_hours: int,
    session: Session,
) -> ThroughputResponse:
    cutoff = _window_start(window_hours)
    runs = session.execute(
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
    ).scalars().all()

    grouped: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    jobs: list[JobThroughput] = []
    for job in sorted(grouped):
        job_runs = grouped[job]
        statuses = [(run.status or "").strip().lower() for run in job_runs]
        durations = [
            duration
            for run in job_runs
            if (duration := _duration_seconds(run)) is not None
        ]
        last_run = job_runs[-1]

        jobs.append(
            JobThroughput(
                job=job,
                runs=len(job_runs),
                ok=sum(status in {"ok", "success"} for status in statuses),
                failed=sum(status in {"failed", "error"} for status in statuses),
                avg_seconds=(sum(durations) / len(durations)) if durations else None,
                last_status=last_run.status,
                last_started_at=last_run.started_at,
            )
        )

    return ThroughputResponse(window_hours=window_hours, jobs=jobs)


router = APIRouter(prefix="/api")


@router.get("/cadence/throughput", response_model=ThroughputResponse)
def get_cadence_window_throughput(
    window_hours: int = Query(default=24),
    session: Session = Depends(get_session),
) -> ThroughputResponse:
    window_hours = max(1, min(720, window_hours))
    return cadence_window_throughput(window_hours, session)


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="daily-index",
                    status="ok",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3, minutes=-55),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    job="nightly-refresh",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2, minutes=-59),
                    rows_affected=0,
                    detail=None,
                ),
                CadenceJobRun(
                    job="nightly-refresh",
                    status="success",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1, minutes=-59),
                    rows_affected=5,
                    detail=None,
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/cadence/throughput", params={"window_hours": 24})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["jobs"]) == 2, payload
            failed_job = next(
                job for job in payload["jobs"] if job["job"] == "nightly-refresh"
            )
            assert failed_job["failed"] == 1, payload
        print("PASS")
    finally:
        test_engine.dispose()


if __name__ == "__main__":
    _run_self_test()