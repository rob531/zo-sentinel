from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


def _clamp_window_hours(window_hours: int) -> int:
    return max(1, min(720, window_hours))


def _duration_seconds(run: CadenceJobRun) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def _last_started_at(run: CadenceJobRun) -> str | None:
    if run.started_at is None:
        return None
    return run.started_at.isoformat()


def _job_sort_key(run: CadenceJobRun) -> tuple[datetime, int]:
    started_at = run.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=None)
    return started_at, run.id


def get_cadence_window_throughput(
    window_hours: int = 24,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    hours = _clamp_window_hours(window_hours)
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    runs = (
        session.execute(
            select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
        )
        .scalars()
        .all()
    )

    grouped: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    jobs: list[dict[str, Any]] = []
    for job_name in sorted(grouped):
        job_runs = grouped[job_name]
        ok = sum(1 for run in job_runs if (run.status or "").lower() in {"ok", "success"})
        failed = sum(
            1 for run in job_runs if (run.status or "").lower() in {"failed", "error"}
        )
        durations = [
            duration
            for run in job_runs
            if (duration := _duration_seconds(run)) is not None
        ]
        last_run = max(job_runs, key=_job_sort_key)

        jobs.append(
            {
                "job": job_name,
                "runs": len(job_runs),
                "ok": ok,
                "failed": failed,
                "avg_seconds": (sum(durations) / len(durations)) if durations else None,
                "last_status": last_run.status,
                "last_started_at": _last_started_at(last_run),
            }
        )

    return {"window_hours": hours, "jobs": jobs}


get_throughput = get_cadence_window_throughput


@router.get("/cadence/throughput")
def cadence_throughput(
    window_hours: int = Query(24),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_cadence_window_throughput(window_hours=window_hours, session=session)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with TestSession() as test_session:
        test_session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="ingest",
                    status="ok",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29, seconds=30),
                    rows_affected=10,
                ),
                CadenceJobRun(
                    id=2,
                    job="ingest",
                    status="failed",
                    started_at=now - timedelta(minutes=20),
                    finished_at=now - timedelta(minutes=19, seconds=30),
                    rows_affected=0,
                ),
                CadenceJobRun(
                    id=3,
                    job="scoring",
                    status="success",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9, seconds=30),
                    rows_affected=4,
                ),
            ]
        )
        test_session.commit()

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = lambda: test_session

        client = TestClient(that_app)
        response = client.get("/api/cadence/throughput", params={"window_hours": 24})

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        failed_jobs = [job for job in payload["jobs"] if job["job"] == "ingest"]
        assert len(failed_jobs) == 1
        assert failed_jobs[0]["failed"] == 1

        print("PASS")