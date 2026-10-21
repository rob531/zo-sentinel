from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


SUCCESS_STATUSES = {"success", "ok", "completed"}


def _duration_seconds(run: CadenceJobRun) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    started_at = run.started_at
    finished_at = run.finished_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    return max(0.0, (finished_at - started_at).total_seconds())


def _nearest_rank_p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def get_cadence_sla(
    hours: int = 24,
    threshold_seconds: int = 300,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    window_start = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = session.execute(
        select(CadenceJobRun).where(CadenceJobRun.started_at >= window_start)
    )
    runs = list(result.scalars().all())

    by_job: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        by_job.setdefault(run.job, []).append(run)

    jobs: list[dict[str, Any]] = []
    for job, job_runs in by_job.items():
        durations = [
            duration
            for duration in (_duration_seconds(run) for run in job_runs)
            if duration is not None
        ]
        failures = sum(1 for run in job_runs if run.status not in SUCCESS_STATUSES)
        run_count = len(job_runs)
        success_rate_pct = (
            round((run_count - failures) * 100.0 / run_count, 2)
            if run_count
            else 0.0
        )
        max_seconds = round(max(durations), 2) if durations else 0.0
        avg_seconds = round(sum(durations) / len(durations), 2) if durations else 0.0
        p95_seconds = round(_nearest_rank_p95(durations), 2)
        rows_affected_total = sum(
            int(run.rows_affected or 0) for run in job_runs
        )
        last_run_at = max(
            (run.started_at for run in job_runs if run.started_at is not None),
            default=None,
        )
        breached = max_seconds > threshold_seconds or failures > 0

        jobs.append(
            {
                "job": job,
                "runs": run_count,
                "failures": failures,
                "success_rate_pct": success_rate_pct,
                "avg_seconds": avg_seconds,
                "max_seconds": max_seconds,
                "p95_seconds": p95_seconds,
                "rows_affected_total": rows_affected_total,
                "last_run_at": (
                    last_run_at.isoformat() if last_run_at is not None else None
                ),
                "breached": breached,
            }
        )

    jobs.sort(key=lambda item: (item["max_seconds"], item["failures"]), reverse=True)
    breached_count = sum(1 for item in jobs if item["breached"])
    worst_job = jobs[0]["job"] if jobs else ""

    return {
        "hours": hours,
        "threshold_seconds": threshold_seconds,
        "jobs": jobs,
        "breached_count": breached_count,
        "worst_job": worst_job,
    }


router = APIRouter()
router.get("/cadence/sla")(get_cadence_sla)

app = FastAPI()
app.include_router(router, prefix="/api")


def _run_self_test() -> int:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    rows_affected=10,
                    detail="ok",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=now - timedelta(minutes=20),
                    finished_at=now - timedelta(minutes=19),
                    rows_affected=0,
                    detail="failure",
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now,
                    rows_affected=25,
                    detail="ok",
                ),
                CadenceJobRun(
                    job="beta",
                    status="completed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=900),
                    rows_affected=50,
                    detail="slow",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/cadence/sla",
            params={"hours": 24, "threshold_seconds": 300},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["jobs"]) == 2
        failing_job = next(job for job in payload["jobs"] if job["job"] == "alpha")
        assert failing_job["failures"] == 1
        assert failing_job["success_rate_pct"] < 100
        assert payload["breached_count"] >= 1
        assert isinstance(payload["worst_job"], str)
        assert payload["worst_job"]

    engine.dispose()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_self_test())