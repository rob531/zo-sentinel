from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api")


class JobDurationStats(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    avg_duration_seconds: float | None
    max_duration_seconds: float | None
    total_rows_affected: int
    last_started_at: str | None
    last_status: str | None


class JobDurationStatsResponse(BaseModel):
    total_runs: int
    jobs: list[JobDurationStats]


_TOTAL_RUNS_SQL = text(
    """
    SELECT COUNT(*) AS total_runs
    FROM cadence_job_runs
    """
)

_JOB_STATS_SQL = text(
    """
    WITH grouped AS (
        SELECT
            job,
            COUNT(*) AS runs,
            COALESCE(
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END),
                0
            ) AS succeeded,
            COALESCE(
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                0
            ) AS failed,
            COALESCE(
                AVG(
                    CASE
                        WHEN finished_at IS NOT NULL
                             AND started_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (finished_at - started_at))
                    END
                ),
                0
            ) AS avg_duration_seconds,
            COALESCE(
                MAX(
                    CASE
                        WHEN finished_at IS NOT NULL
                             AND started_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (finished_at - started_at))
                    END
                ),
                0
            ) AS max_duration_seconds,
            COALESCE(SUM(COALESCE(rows_affected, 0)), 0)
                AS total_rows_affected,
            MAX(started_at) AS last_started_at
        FROM cadence_job_runs
        GROUP BY job
    ),
    latest AS (
        SELECT DISTINCT ON (job)
            job,
            status
        FROM cadence_job_runs
        ORDER BY job, started_at DESC NULLS LAST, id DESC
    )
    SELECT
        grouped.job,
        grouped.runs,
        grouped.succeeded,
        grouped.failed,
        grouped.avg_duration_seconds,
        grouped.max_duration_seconds,
        grouped.total_rows_affected,
        grouped.last_started_at,
        latest.status AS last_status
    FROM grouped
    JOIN latest ON latest.job = grouped.job
    ORDER BY grouped.job
    """
)


def _iso_timestamp(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _number(value):
    if value is None:
        return None
    return float(value)


async def get_cadence_job_duration_stats(
    session=Depends(get_session),
) -> JobDurationStatsResponse:
    total_result = session.execute(_TOTAL_RUNS_SQL)
    total_row = total_result.mappings().first()
    total_runs = int(total_row["total_runs"]) if total_row else 0

    result = session.execute(_JOB_STATS_SQL)
    jobs = [
        JobDurationStats(
            job=str(row["job"]),
            runs=int(row["runs"]),
            succeeded=int(row["succeeded"]),
            failed=int(row["failed"]),
            avg_duration_seconds=_number(row["avg_duration_seconds"]),
            max_duration_seconds=_number(row["max_duration_seconds"]),
            total_rows_affected=int(row["total_rows_affected"]),
            last_started_at=_iso_timestamp(row["last_started_at"]),
            last_status=(
                str(row["last_status"])
                if row["last_status"] is not None
                else None
            ),
        )
        for row in result.mappings().all()
    ]

    return JobDurationStatsResponse(total_runs=total_runs, jobs=jobs)


@router.get(
    "/cadence/job-duration-stats",
    response_model=JobDurationStatsResponse,
)
async def get_job_duration_stats(
    session=Depends(get_session),
) -> JobDurationStatsResponse:
    return await get_cadence_job_duration_stats(session=session)


async def endpoint(session=Depends(get_session)):
    return await get_cadence_job_duration_stats(session=session)


get_stats = get_cadence_job_duration_stats


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    seeded_rows = [
        {
            "id": 1,
            "job": "alpha",
            "status": "success",
            "started_at": base,
            "finished_at": base + timedelta(seconds=2),
            "rows_affected": 10,
        },
        {
            "id": 2,
            "job": "alpha",
            "status": "success",
            "started_at": base + timedelta(minutes=1),
            "finished_at": base + timedelta(minutes=1, seconds=4),
            "rows_affected": 20,
        },
        {
            "id": 3,
            "job": "alpha",
            "status": "failed",
            "started_at": base + timedelta(minutes=2),
            "finished_at": None,
            "rows_affected": 0,
        },
        {
            "id": 4,
            "job": "beta",
            "status": "success",
            "started_at": base + timedelta(minutes=3),
            "finished_at": base + timedelta(minutes=3, seconds=6),
            "rows_affected": 30,
        },
        {
            "id": 5,
            "job": "beta",
            "status": "failed",
            "started_at": base + timedelta(minutes=4),
            "finished_at": base + timedelta(minutes=4, seconds=1),
            "rows_affected": 5,
        },
    ]

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def first(self):
            return self._rows[0] if self._rows else None

        def all(self):
            return self._rows

    class _InMemorySession:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "COUNT(*) AS total_runs" in sql:
                return _Result([{"total_runs": len(seeded_rows)}])

            grouped = {}
            for row in seeded_rows:
                job = grouped.setdefault(
                    row["job"],
                    {
                        "job": row["job"],
                        "runs": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "durations": [],
                        "total_rows_affected": 0,
                        "last_started_at": None,
                        "last_status": None,
                    },
                )
                job["runs"] += 1
                if row["status"] == "success":
                    job["succeeded"] += 1
                if row["status"] == "failed":
                    job["failed"] += 1
                job["total_rows_affected"] += row["rows_affected"] or 0
                if (
                    job["last_started_at"] is None
                    or row["started_at"] > job["last_started_at"]
                ):
                    job["last_started_at"] = row["started_at"]
                    job["last_status"] = row["status"]
                if row["finished_at"] is not None:
                    job["durations"].append(
                        (row["finished_at"] - row["started_at"]).total_seconds()
                    )

            rows = []
            for job in grouped.values():
                durations = job.pop("durations")
                rows.append(
                    {
                        **job,
                        "avg_duration_seconds": (
                            sum(durations) / len(durations) if durations else 0
                        ),
                        "max_duration_seconds": max(durations) if durations else 0,
                    }
                )
            return _Result(rows)

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: _InMemorySession()

    response = TestClient(test_app).get("/api/cadence/job-duration-stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_runs"] == 5
    assert len(payload["jobs"]) == 2
    for job in payload["jobs"]:
        assert job["runs"] >= 1
        assert job["avg_duration_seconds"] is None or isinstance(
            job["avg_duration_seconds"], float
        )
    print("PASS")