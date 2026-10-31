from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api/cadence", tags=["cadence"])


def _utc_now() -> datetime:
    return datetime.utcnow()


def _parse_detail(detail: Any) -> Any:
    if detail is None:
        return None
    if isinstance(detail, (dict, list)):
        return detail
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", errors="replace")
    if isinstance(detail, str):
        try:
            return json.loads(detail)
        except (TypeError, ValueError):
            return detail
    return detail


def _detail_text(detail: Any) -> str:
    parsed = _parse_detail(detail)
    if isinstance(parsed, str):
        return parsed.lower()
    try:
        return json.dumps(parsed, sort_keys=True, default=str).lower()
    except (TypeError, ValueError):
        return str(parsed).lower()


def _classify_failure(detail: Any) -> str:
    text = _detail_text(detail)
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "schema" in text:
        return "schema"
    if "upstream" in text:
        return "upstream_error"
    if "empty_result" in text or "empty result" in text:
        return "empty_result"
    return "unknown"


def _duration_seconds(started_at: datetime, finished_at: datetime | None) -> float | None:
    if finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


def _duration_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return "under_60s"
    if seconds < 300:
        return "60s_to_5m"
    if seconds < 900:
        return "5m_to_15m"
    return "over_15m"


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("/failures")
def list_cadence_failures(
    days: int = 30,
    job: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if days < 1:
        days = 1
    if days > 3650:
        days = 3650

    cutoff = _utc_now() - timedelta(days=days)
    statement = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    runs = list(session.execute(statement).scalars().all())
    failed_by_job: dict[str, list[CadenceJobRun]] = {}
    total_by_job: dict[str, int] = {}

    for run in runs:
        job_name = str(run.job)
        total_by_job[job_name] = total_by_job.get(job_name, 0) + 1
        status = str(run.status or "").strip().lower()
        if status in {"failed", "errored", "error"}:
            failed_by_job.setdefault(job_name, []).append(run)

    jobs: list[dict[str, Any]] = []
    for job_name in sorted(failed_by_job):
        failures = failed_by_job[job_name]
        counts = {
            "timeout": 0,
            "schema": 0,
            "upstream_error": 0,
            "empty_result": 0,
            "unknown": 0,
        }
        last_failure: datetime | None = None

        for run in failures:
            bucket = _classify_failure(run.detail)
            counts[bucket] += 1

            duration_seconds = _duration_seconds(run.started_at, run.finished_at)
            _duration_bucket(duration_seconds)

            candidate = run.finished_at or run.started_at
            if candidate is not None:
                if last_failure is None or candidate > last_failure:
                    last_failure = candidate

        total_runs = total_by_job.get(job_name, 0)
        failed_runs = len(failures)
        jobs.append(
            {
                "job": job_name,
                "total_runs": total_runs,
                "failed_runs": failed_runs,
                "failure_rate": failed_runs / total_runs if total_runs else 0.0,
                "buckets": counts,
                "last_failure_at": _isoformat(last_failure),
            }
        )

    return {"days": days, "jobs": jobs}


app = FastAPI(title="Cadence Job Failure Taxonomy")
app.include_router(router)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        test_session = TestingSession()
        try:
            yield test_session
        finally:
            test_session.close()

    now = datetime.utcnow()
    seed_rows = [
        CadenceJobRun(
            id=1,
            job="alpha",
            status="failed",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            rows_affected=0,
            detail={"error": "timeout"},
        ),
        CadenceJobRun(
            id=2,
            job="alpha",
            status="failed",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=8),
            rows_affected=0,
            detail={"error": "timeout"},
        ),
        CadenceJobRun(
            id=3,
            job="beta",
            status="failed",
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=14),
            rows_affected=0,
            detail={"error": "schema violation"},
        ),
        CadenceJobRun(
            id=4,
            job="alpha",
            status="ok",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            rows_affected=10,
            detail={},
        ),
        CadenceJobRun(
            id=5,
            job="alpha",
            status="ok",
            started_at=now - timedelta(minutes=3),
            finished_at=now - timedelta(minutes=2),
            rows_affected=12,
            detail={},
        ),
        CadenceJobRun(
            id=6,
            job="beta",
            status="ok",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            rows_affected=8,
            detail={},
        ),
    ]

    seed_session = TestingSession()
    seed_session.add_all(seed_rows)
    seed_session.commit()
    seed_session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/failures", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        assert sum(item["buckets"]["timeout"] for item in payload["jobs"]) == 2
        assert all(0 <= item["failure_rate"] <= 1 for item in payload["jobs"])

    print("PASS")