from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import get_cadence_job_failures as _logic_get_cadence_job_failures
except ImportError:
    _logic_get_cadence_job_failures = None


router = APIRouter(prefix="/api", tags=["cadence"])

_REQUIRED_JOB_FIELDS = {
    "job",
    "run_count",
    "failure_count",
    "failure_rate",
    "last_status",
    "last_started_at",
    "last_finished_at",
    "avg_duration_seconds",
    "last_error_detail",
}


def _truncate(value: str) -> str:
    if len(value) <= 500:
        return value
    return value[:497] + "..."


def _detail_text(detail: Any) -> str | None:
    if detail is None:
        return None

    if isinstance(detail, str):
        text = detail.strip()
        if not text:
            return None
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            return _truncate(text)
        return _detail_text(decoded)

    if isinstance(detail, dict):
        for key in ("error", "error_detail", "exception", "message", "reason"):
            if key in detail and detail[key] not in (None, ""):
                value = detail[key]
                if not isinstance(value, str):
                    value = json.dumps(value, sort_keys=True, default=str)
                return _truncate(value)
        text = json.dumps(detail, sort_keys=True, default=str)
        return _truncate(text) if text else None

    if isinstance(detail, (list, tuple)):
        text = json.dumps(detail, default=str)
        return _truncate(text) if text else None

    return _truncate(str(detail))


def _is_failure(status: Any) -> bool:
    return str(status or "").strip().lower() in {"failed", "failure", "error"}


def _query_job_failures(session: Session, hours: int) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    runs = list(
        session.scalars(
            select(CadenceJobRun)
            .where(CadenceJobRun.started_at >= cutoff)
            .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        ).all()
    )

    grouped: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    jobs: list[dict[str, Any]] = []
    for job, job_runs in grouped.items():
        failure_count = sum(1 for run in job_runs if _is_failure(run.status))
        run_count = len(job_runs)
        last_run = max(
            job_runs,
            key=lambda run: (
                run.started_at or datetime.min,
                run.id or 0,
            ),
        )

        durations = [
            (run.finished_at - run.started_at).total_seconds()
            for run in job_runs
            if run.started_at is not None
            and run.finished_at is not None
            and run.finished_at >= run.started_at
        ]
        avg_duration = round(sum(durations) / len(durations), 3) if durations else None

        error_detail = None
        for run in job_runs:
            if _is_failure(run.status):
                error_detail = _detail_text(run.detail)
                if error_detail is not None:
                    break

        jobs.append(
            {
                "job": job,
                "run_count": run_count,
                "failure_count": failure_count,
                "failure_rate": round(failure_count / run_count, 6),
                "last_status": last_run.status,
                "last_started_at": last_run.started_at,
                "last_finished_at": last_run.finished_at,
                "avg_duration_seconds": avg_duration,
                "last_error_detail": error_detail,
            }
        )

    jobs.sort(key=lambda item: item["job"])
    return {"hours": hours, "jobs": jobs}


def _get_cadence_job_failures(session: Session, hours: int) -> dict[str, Any]:
    if _logic_get_cadence_job_failures is not None:
        try:
            result = _logic_get_cadence_job_failures(session, hours)
            if (
                isinstance(result, dict)
                and result.get("hours") == hours
                and isinstance(result.get("jobs"), list)
                and all(
                    isinstance(job, dict) and _REQUIRED_JOB_FIELDS.issubset(job)
                    for job in result["jobs"]
                )
            ):
                return result
        except (TypeError, ValueError, KeyError):
            pass
    return _query_job_failures(session, hours)


@router.get("/cadence/jobs/failures")
def get_cadence_job_failures(
    hours: int = Query(default=24),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    bounded_hours = max(1, min(720, hours))
    return _get_cadence_job_failures(session, bounded_hours)


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    rows_affected=0,
                    detail={"error": "database timeout"},
                ),
                CadenceJobRun(
                    job="failing-job",
                    status="success",
                    started_at=now - timedelta(minutes=20),
                    finished_at=now - timedelta(minutes=19),
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=0,
                    detail={"error": "database timeout"},
                ),
                CadenceJobRun(
                    job="healthy-job",
                    status="success",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=10,
                    detail={},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/jobs/failures", params={"hours": 24})
        assert response.status_code == 200
        payload = response.json()
        assert payload["hours"] == 24
        jobs_by_name = {job["job"]: job for job in payload["jobs"]}
        assert jobs_by_name["failing-job"]["failure_count"] == 2
        assert jobs_by_name["failing-job"]["failure_rate"] > 0.6
        assert jobs_by_name["healthy-job"]["failure_count"] == 0

    print("PASS")