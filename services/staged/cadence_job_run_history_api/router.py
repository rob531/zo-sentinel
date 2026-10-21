from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import *  # noqa: F401,F403
except ImportError:
    from logic import *  # noqa: F401,F403


router = APIRouter(prefix="/api")

SUCCESS_STATUSES = ("success", "succeeded", "completed", "ok")


class JobRunSummary(BaseModel):
    job: str
    total_runs: int
    successes: int
    failures: int
    success_rate_pct: float
    rows_affected: int
    avg_duration_seconds: float
    last_status: Optional[str]
    last_started_at: Optional[datetime]


class CadenceJobRunHistoryResponse(BaseModel):
    jobs: List[JobRunSummary]
    job_names: List[str]


def _duration_seconds(started_at: object, finished_at: object) -> Optional[float]:
    if started_at is None or finished_at is None:
        return None

    def parse(value: object) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return None
            if candidate.endswith("Z"):
                candidate = candidate[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                return None
        return None

    started = parse(started_at)
    finished = parse(finished_at)
    if started is None or finished is None:
        return None

    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)

    duration = (finished - started).total_seconds()
    return duration if duration >= 0 else None


def _read_job_run_history(
    session: Session,
    job: Optional[str],
    days: int,
) -> dict:
    bound = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    summary_sql = text(
        """
        WITH ranked AS (
            SELECT
                job,
                status,
                started_at,
                rows_affected,
                ROW_NUMBER() OVER (
                    PARTITION BY job
                    ORDER BY started_at DESC, id DESC
                ) AS run_rank
            FROM cadence_job_runs
            WHERE started_at >= :bound
              AND (:job IS NULL OR job = :job)
        )
        SELECT
            job,
            COUNT(*) AS total_runs,
            SUM(
                CASE
                    WHEN LOWER(status) IN ('success', 'succeeded', 'completed', 'ok')
                    THEN 1
                    ELSE 0
                END
            ) AS successes,
            SUM(COALESCE(rows_affected, 0)) AS rows_affected,
            MAX(CASE WHEN run_rank = 1 THEN status END) AS last_status,
            MAX(CASE WHEN run_rank = 1 THEN started_at END) AS last_started_at
        FROM ranked
        GROUP BY job
        ORDER BY job
        """
    )

    duration_sql = text(
        """
        SELECT job, started_at, finished_at
        FROM cadence_job_runs
        WHERE started_at >= :bound
          AND (:job IS NULL OR job = :job)
        ORDER BY job, started_at DESC, id DESC
        """
    )

    names_sql = text(
        """
        SELECT job
        FROM cadence_job_runs
        GROUP BY job
        ORDER BY job
        """
    )

    summary_rows = session.execute(
        summary_sql,
        {"bound": bound, "job": job},
    ).mappings()

    duration_rows = session.execute(
        duration_sql,
        {"bound": bound, "job": job},
    ).mappings()

    durations: dict[str, List[float]] = {}
    for row in duration_rows:
        duration = _duration_seconds(row["started_at"], row["finished_at"])
        if duration is not None:
            durations.setdefault(str(row["job"]), []).append(duration)

    jobs: List[JobRunSummary] = []
    for row in summary_rows:
        job_name = str(row["job"])
        total_runs = int(row["total_runs"] or 0)
        successes = int(row["successes"] or 0)
        failures = total_runs - successes
        success_rate = round((successes / total_runs) * 100.0, 2) if total_runs else 0.0
        job_durations = durations.get(job_name, [])
        average_duration = (
            round(sum(job_durations) / len(job_durations), 2)
            if job_durations
            else 0.0
        )

        jobs.append(
            JobRunSummary(
                job=job_name,
                total_runs=total_runs,
                successes=successes,
                failures=failures,
                success_rate_pct=success_rate,
                rows_affected=int(row["rows_affected"] or 0),
                avg_duration_seconds=average_duration,
                last_status=(
                    str(row["last_status"])
                    if row["last_status"] is not None
                    else None
                ),
                last_started_at=row["last_started_at"],
            )
        )

    job_names = [str(row["job"]) for row in session.execute(names_sql).mappings()]

    return {
        "jobs": jobs,
        "job_names": job_names,
    }


@router.get(
    "/cadence/jobs/runs",
    response_model=CadenceJobRunHistoryResponse,
)
def get_cadence_job_runs(
    job: Optional[str] = Query(default=None),
    days: int = Query(default=14, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict:
    return _read_job_run_history(session=session, job=job, days=days)


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as setup_session:
        setup_session.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    rows_affected INTEGER,
                    detail TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        setup_session.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, started_at, finished_at, rows_affected, detail)
                VALUES
                    (1, 'alpha', 'success', :started_1, :finished_1, 10, NULL),
                    (2, 'alpha', 'failed', :started_2, :finished_2, 0, NULL),
                    (3, 'beta', 'success', :started_3, :finished_3, 7, NULL)
                """
            ),
            {
                "started_1": (now - timedelta(hours=3)).isoformat(),
                "finished_1": (now - timedelta(hours=3, seconds=-30)).isoformat(),
                "started_2": (now - timedelta(hours=2)).isoformat(),
                "finished_2": (now - timedelta(hours=2, seconds=-45)).isoformat(),
                "started_3": (now - timedelta(hours=1)).isoformat(),
                "finished_3": (now - timedelta(hours=1, seconds=-20)).isoformat(),
            },
        )
        setup_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/cadence/jobs/runs",
        params={"job": "alpha", "days": 14},
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    alpha = next(item for item in payload["jobs"] if item["job"] == "alpha")
    assert alpha["failures"] == 1, payload
    assert alpha["success_rate_pct"] == 50.0, payload
    assert set(payload["job_names"]) == {"alpha", "beta"}, payload

    print("PASS")