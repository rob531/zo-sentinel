from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


TAXONOMY_BUCKETS = (
    "timeout",
    "schema",
    "upstream_error",
    "empty_result",
    "unknown",
)
FAILURE_STATUSES = {"failed", "error", "errored"}


def _detail_data(detail: Any) -> Any:
    if isinstance(detail, str):
        try:
            return json.loads(detail)
        except (TypeError, ValueError):
            return detail
    return detail


def _failure_bucket(detail: Any) -> str:
    data = _detail_data(detail)
    if isinstance(data, dict):
        error = str(data.get("error", "")).lower()
        text = json.dumps(data, sort_keys=True, default=str).lower()
    else:
        error = str(data or "").lower()
        text = error

    combined = f"{error} {text}"
    if "timeout" in combined or "timed out" in combined:
        return "timeout"
    if "schema" in combined:
        return "schema"
    if "upstream" in combined:
        return "upstream_error"
    if "empty_result" in combined or "empty result" in combined or "no rows" in combined:
        return "empty_result"
    return "unknown"


def _duration_seconds(run: CadenceJobRun) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    started = run.started_at
    finished = run.finished_at
    if started.tzinfo is None and finished.tzinfo is not None:
        finished = finished.replace(tzinfo=None)
    elif finished.tzinfo is None and started.tzinfo is not None:
        started = started.replace(tzinfo=None)
    return max(0.0, (finished - started).total_seconds())


def _duration_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return "under_1m"
    if seconds < 300:
        return "1m_to_5m"
    if seconds < 3600:
        return "5m_to_1h"
    return "over_1h"


def _iso8601(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def get_failure_taxonomy(
    session: Session,
    days: int,
    job: str | None = None,
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    runs = list(session.scalars(statement).all())
    grouped: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    jobs: list[dict[str, Any]] = []
    for job_name in sorted(grouped):
        job_runs = grouped[job_name]
        failed_runs = [
            run for run in job_runs if run.status.lower() in FAILURE_STATUSES
        ]
        buckets = {bucket: 0 for bucket in TAXONOMY_BUCKETS}
        last_failure_at: datetime | None = None

        for run in failed_runs:
            buckets[_failure_bucket(run.detail)] += 1
            failure_time = run.finished_at or run.started_at
            if failure_time is not None:
                if last_failure_at is None or failure_time > last_failure_at:
                    last_failure_at = failure_time

            duration_seconds = _duration_seconds(run)
            _duration_bucket(duration_seconds)

        total_runs = len(job_runs)
        failed_count = len(failed_runs)
        jobs.append(
            {
                "job": job_name,
                "total_runs": total_runs,
                "failed_runs": failed_count,
                "failure_rate": failed_count / total_runs if total_runs else 0.0,
                "buckets": buckets,
                "last_failure_at": _iso8601(last_failure_at),
            }
        )

    return {"days": days, "jobs": jobs}


router = APIRouter(prefix="/api")


@router.get("/cadence/failures")
def cadence_failures(
    days: int = Query(default=30, ge=1, le=3650),
    job: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_failure_taxonomy(session, days=days, job=job)


app = FastAPI(title="Cadence Job Failure Taxonomy")
app.include_router(router)


def _run_self_test() -> None:
    that_app = FastAPI()
    that_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    CadenceJobRun.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        CadenceJobRun(
            id=1,
            job="alpha",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2) + timedelta(seconds=30),
            rows_affected=0,
            detail={"error": "timeout"},
        ),
        CadenceJobRun(
            id=2,
            job="alpha",
            status="failed",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1) + timedelta(seconds=45),
            rows_affected=0,
            detail={"error": "timeout"},
        ),
        CadenceJobRun(
            id=3,
            job="alpha",
            status="ok",
            started_at=now - timedelta(minutes=30),
            finished_at=now - timedelta(minutes=29),
            rows_affected=10,
            detail=None,
        ),
        CadenceJobRun(
            id=4,
            job="beta",
            status="failed",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=3) + timedelta(seconds=10),
            rows_affected=0,
            detail={"error": "schema violation"},
        ),
        CadenceJobRun(
            id=5,
            job="beta",
            status="ok",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            rows_affected=5,
            detail=None,
        ),
        CadenceJobRun(
            id=6,
            job="beta",
            status="ok",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            rows_affected=7,
            detail=None,
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/failures", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert len(payload["jobs"]) == 2, payload

    timeout_count = sum(job["buckets"]["timeout"] for job in payload["jobs"])
    assert timeout_count == 2, payload
    assert all(0.0 <= job["failure_rate"] <= 1.0 for job in payload["jobs"]), payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()