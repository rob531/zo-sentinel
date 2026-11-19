"""Logic for the cadence job failure digest service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


SUCCESS_STATUSES = {"success", "ok", "completed"}


class JobFailureDigest(BaseModel):
    job: str
    runs: int
    failures: int
    partials: int
    success_rate: float
    rows_affected_avg: Optional[float]
    last_failure_at: Optional[str]
    last_error: Optional[str]


class FailureDigestResponse(BaseModel):
    generated_at: str
    window_hours: int
    jobs: List[JobFailureDigest]
    worst_jobs: List[str]


router = APIRouter(prefix="/api", tags=["cadence"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _error_snippet(detail: Any) -> Optional[str]:
    if not isinstance(detail, dict):
        return None
    value = detail.get("error") or detail.get("message")
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:1000]


def build_failure_digest(db: Session, hours: int = 24) -> Dict[str, Any]:
    """Build a failure digest for cadence runs in the requested time window."""
    window_hours = max(1, min(int(hours), 720))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    runs = list(
        db.execute(
            select(CadenceJobRun)
            .where(CadenceJobRun.started_at >= cutoff)
            .order_by(CadenceJobRun.started_at.desc())
        )
        .scalars()
        .all()
    )

    grouped: Dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        job = str(run.job)
        grouped.setdefault(job, []).append(run)

    jobs: List[Dict[str, Any]] = []
    for job, job_runs in grouped.items():
        total = len(job_runs)
        failures = sum(
            1 for run in job_runs if run.status not in SUCCESS_STATUSES
        )
        partials = sum(1 for run in job_runs if run.status == "partial")
        successful = [run for run in job_runs if run.status in SUCCESS_STATUSES]
        rows_values = [
            int(run.rows_affected)
            for run in successful
            if run.rows_affected is not None
        ]

        latest_failure: Optional[CadenceJobRun] = None
        latest_error: Optional[str] = None
        for run in job_runs:
            if run.status in SUCCESS_STATUSES:
                continue
            if latest_failure is None:
                latest_failure = run
            if latest_error is None:
                latest_error = _error_snippet(run.detail)

        rows_avg = (
            float(sum(rows_values) / len(rows_values))
            if rows_values
            else None
        )

        jobs.append(
            {
                "job": job,
                "runs": total,
                "failures": failures,
                "partials": partials,
                "success_rate": float(len(successful) / total) if total else 0.0,
                "rows_affected_avg": rows_avg,
                "last_failure_at": _iso(
                    latest_failure.finished_at if latest_failure else None
                ),
                "last_error": latest_error,
            }
        )

    jobs.sort(key=lambda item: (item["job"],))
    worst_jobs = [
        item["job"]
        for item in sorted(
            (
                item
                for item in jobs
                if item["failures"] > 0 or item["partials"] > 0
            ),
            key=lambda item: (
                -item["failures"],
                -item["partials"],
                -item["runs"],
                item["job"],
            ),
        )
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "jobs": jobs,
        "worst_jobs": worst_jobs,
    }


def get_failure_digest(
    db: Session = Depends(get_session),
    hours: int = Query(default=24, ge=1, le=720),
) -> FailureDigestResponse:
    """Return the cadence failure digest."""
    return FailureDigestResponse(**build_failure_digest(db, hours))


router.add_api_route(
    "/cadence/failure-digest",
    get_failure_digest,
    methods=["GET"],
    response_model=FailureDigestResponse,
)


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
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as test_db:
        test_db.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="healthy-job",
                    status="success",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    id=2,
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(minutes=3),
                    finished_at=now - timedelta(minutes=2),
                    rows_affected=0,
                    detail={"error": "boom"},
                ),
                CadenceJobRun(
                    id=3,
                    job="failing-job",
                    status="success",
                    started_at=now - timedelta(minutes=1),
                    finished_at=now,
                    rows_affected=5,
                    detail={},
                ),
            ]
        )
        test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as test_db:
            yield test_db

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/failure-digest?hours=24")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        failing = next(item for item in payload["jobs"] if item["job"] == "failing-job")
        assert failing["failures"] == 1
        assert failing["last_error"] == "boom"
        assert payload["worst_jobs"][0] == "failing-job"

    print("PASS")