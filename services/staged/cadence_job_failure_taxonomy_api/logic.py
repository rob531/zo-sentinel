from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


SUCCESS_STATUSES = {"success", "ok", "completed", "done"}
DEFAULT_DAYS = 7
MIN_DAYS = 1
MAX_DAYS = 90


class JobFailureTaxonomy(BaseModel):
    job: str
    total_runs: int
    failures: int
    failure_rate: float
    by_detail_reason: Dict[str, int]
    last_failure_at: Optional[str]
    last_success_at: Optional[str]
    median_duration_seconds: Optional[float]


class FailureTaxonomyResponse(BaseModel):
    days: int
    jobs: List[JobFailureTaxonomy]
    worst_job: Optional[str]
    generated_at: str


router = APIRouter(prefix="/api")


def _clamp_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, value))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _cutoff(days: int) -> datetime:
    value = _utc_now() - timedelta(days=days)
    column_type = CadenceJobRun.started_at.property.columns[0].type
    if getattr(column_type, "timezone", False):
        return value
    return value.replace(tzinfo=None)


def _detail_payload(detail: Any) -> Any:
    if detail is None:
        return None
    if isinstance(detail, (dict, list)):
        return detail
    if isinstance(detail, bytes):
        try:
            detail = detail.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(detail, str):
        try:
            return json.loads(detail)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return None


def _reason_label(detail: Any) -> str:
    payload = _detail_payload(detail)
    if not isinstance(payload, dict):
        return "unspecified"

    value = payload.get("reason")
    if value is None:
        value = payload.get("error")
    if value is None or value == "":
        return "unspecified"
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def get_failure_taxonomy(session: Session, days: int = DEFAULT_DAYS) -> Dict[str, Any]:
    days = _clamp_days(days)
    statement = select(
        CadenceJobRun.id,
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
        CadenceJobRun.rows_affected,
        CadenceJobRun.detail,
    ).where(CadenceJobRun.started_at >= _cutoff(days))

    jobs: Dict[str, Dict[str, Any]] = {}

    for row in session.execute(statement).all():
        job_name = str(row.job)
        job = jobs.setdefault(
            job_name,
            {
                "job": job_name,
                "total_runs": 0,
                "failures": 0,
                "by_detail_reason": {},
                "last_failure_at": None,
                "last_success_at": None,
                "durations": [],
            },
        )

        job["total_runs"] += 1
        status = str(row.status or "").strip().lower()
        started_at = row.started_at
        finished_at = row.finished_at

        if started_at is not None:
            started_utc = _as_utc(started_at)
            if status not in SUCCESS_STATUSES:
                job["failures"] += 1
                reason = _reason_label(row.detail)
                job["by_detail_reason"][reason] = (
                    job["by_detail_reason"].get(reason, 0) + 1
                )
                if (
                    job["last_failure_at"] is None
                    or started_utc > job["last_failure_at"]
                ):
                    job["last_failure_at"] = started_utc
            else:
                if (
                    job["last_success_at"] is None
                    or started_utc > job["last_success_at"]
                ):
                    job["last_success_at"] = started_utc

        if started_at is not None and finished_at is not None:
            duration = (_as_utc(finished_at) - _as_utc(started_at)).total_seconds()
            job["durations"].append(duration)

    results: List[Dict[str, Any]] = []
    for job_name in sorted(jobs):
        job = jobs[job_name]
        total_runs = int(job["total_runs"])
        failures = int(job["failures"])
        durations = job["durations"]
        results.append(
            {
                "job": job_name,
                "total_runs": total_runs,
                "failures": failures,
                "failure_rate": failures / total_runs if total_runs else 0.0,
                "by_detail_reason": {
                    reason: int(count)
                    for reason, count in sorted(job["by_detail_reason"].items())
                },
                "last_failure_at": _iso8601(job["last_failure_at"]),
                "last_success_at": _iso8601(job["last_success_at"]),
                "median_duration_seconds": (
                    float(statistics.median(durations)) if durations else None
                ),
            }
        )

    failed_jobs = [job for job in results if job["failures"] > 0]
    worst_job = (
        max(
            failed_jobs,
            key=lambda job: (
                job["failure_rate"],
                job["failures"],
                job["total_runs"],
                job["job"],
            ),
        )["job"]
        if failed_jobs
        else None
    )

    return {
        "days": days,
        "jobs": results,
        "worst_job": worst_job,
        "generated_at": _utc_now().isoformat(),
    }


def compute_failure_taxonomy(
    session: Session, days: int = DEFAULT_DAYS
) -> Dict[str, Any]:
    return get_failure_taxonomy(session, days)


def get_cadence_job_failure_taxonomy(
    session: Session, days: int = DEFAULT_DAYS
) -> Dict[str, Any]:
    return get_failure_taxonomy(session, days)


@router.get(
    "/cadence/jobs/failure-taxonomy",
    response_model=FailureTaxonomyResponse,
)
def failure_taxonomy_endpoint(
    days: int = Query(DEFAULT_DAYS),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_failure_taxonomy(session, days)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import JSON, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    detail_type = CadenceJobRun.detail.property.columns[0].type
    failure_detail: Any = (
        {"reason": "timeout"}
        if isinstance(detail_type, JSON)
        else json.dumps({"reason": "timeout"})
    )

    testing_session.add_all(
        [
            CadenceJobRun(
                job="failing-job",
                status="success",
                started_at=now - timedelta(minutes=50),
                finished_at=now - timedelta(minutes=49),
                rows_affected=10,
                detail=None,
            ),
            CadenceJobRun(
                job="failing-job",
                status="success",
                started_at=now - timedelta(minutes=40),
                finished_at=now - timedelta(minutes=39),
                rows_affected=12,
                detail=None,
            ),
            CadenceJobRun(
                job="failing-job",
                status="failed",
                started_at=now - timedelta(minutes=30),
                finished_at=now - timedelta(minutes=29),
                rows_affected=0,
                detail=failure_detail,
            ),
            CadenceJobRun(
                job="failing-job",
                status="failed",
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=18),
                rows_affected=0,
                detail=failure_detail,
            ),
            CadenceJobRun(
                job="healthy-job",
                status="success",
                started_at=now - timedelta(minutes=15),
                finished_at=now - timedelta(minutes=14),
                rows_affected=20,
                detail=None,
            ),
            CadenceJobRun(
                job="healthy-job",
                status="completed",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                rows_affected=25,
                detail=None,
            ),
        ]
    )
    testing_session.commit()

    def override_get_session():
        yield testing_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/jobs/failure-taxonomy", params={"days": 7})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 7
        failing = next(
            job for job in payload["jobs"] if job["job"] == "failing-job"
        )
        assert failing["failures"] == 2
        assert failing["failure_rate"] == 0.5
        assert failing["by_detail_reason"]["timeout"] == 2
        assert failing["median_duration_seconds"] is not None
        assert payload["worst_job"] == "failing-job"

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()