from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceJobLedgerEntry(BaseModel):
    job: str
    last_status: Optional[str]
    last_started_at: Optional[str]
    last_finished_at: Optional[str]
    duration_seconds: Optional[float]
    last_rows_affected: Optional[int]
    runs_total: int
    runs_failed: int
    runs_ok: int


class CadenceJobLedgerResponse(BaseModel):
    jobs: list[CadenceJobLedgerEntry]


def _timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get(
    "/cadence/jobs",
    response_model=CadenceJobLedgerResponse,
    summary="List recent cadence job runs",
)
def list_cadence_jobs(
    session: Session = Depends(get_session),
    job: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
) -> CadenceJobLedgerResponse:
    effective_limit = max(1, min(200, limit))
    failed_status = CadenceJobRun.status.not_in(("ok", "success", "completed"))

    ranked = (
        select(
            CadenceJobRun.job.label("job"),
            CadenceJobRun.status.label("status"),
            CadenceJobRun.started_at.label("started_at"),
            CadenceJobRun.finished_at.label("finished_at"),
            CadenceJobRun.rows_affected.label("rows_affected"),
            func.row_number()
            .over(
                partition_by=CadenceJobRun.job,
                order_by=(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc()),
            )
            .label("run_rank"),
            func.count()
            .over(partition_by=CadenceJobRun.job)
            .label("runs_total"),
            func.sum(case((failed_status, 1), else_=0))
            .over(partition_by=CadenceJobRun.job)
            .label("runs_failed"),
        )
        .where(CadenceJobRun.job == job if job is not None else True)
        .subquery()
    )

    rows = session.execute(
        select(
            ranked.c.job,
            ranked.c.status,
            ranked.c.started_at,
            ranked.c.finished_at,
            ranked.c.rows_affected,
            ranked.c.runs_total,
            ranked.c.runs_failed,
        )
        .where(ranked.c.run_rank == 1)
        .order_by(ranked.c.job)
        .limit(effective_limit)
    ).mappings()

    jobs: list[CadenceJobLedgerEntry] = []
    for row in rows:
        started_at = row["started_at"]
        finished_at = row["finished_at"]
        duration_seconds = (
            float((finished_at - started_at).total_seconds())
            if started_at is not None and finished_at is not None
            else None
        )
        runs_total = int(row["runs_total"])
        runs_failed = int(row["runs_failed"] or 0)

        jobs.append(
            CadenceJobLedgerEntry(
                job=str(row["job"]),
                last_status=row["status"],
                last_started_at=_timestamp(started_at),
                last_finished_at=_timestamp(finished_at),
                duration_seconds=duration_seconds,
                last_rows_affected=(
                    int(row["rows_affected"])
                    if row["rows_affected"] is not None
                    else None
                ),
                runs_total=runs_total,
                runs_failed=runs_failed,
                runs_ok=runs_total - runs_failed,
            )
        )

    return CadenceJobLedgerResponse(jobs=jobs)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER NOT NULL PRIMARY KEY,
                    job VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME,
                    rows_affected INTEGER,
                    detail JSON
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, started_at, finished_at, rows_affected, detail)
                VALUES
                    (1, 'alpha', 'ok', :alpha_started, :alpha_finished, 4, '{}'),
                    (2, 'beta', 'failed', :beta_started, :beta_finished, 2, '{}')
                """
            ),
            {
                "alpha_started": datetime(2025, 1, 1, 12, 0, 0),
                "alpha_finished": datetime(2025, 1, 1, 12, 0, 1),
                "beta_started": datetime(2025, 1, 1, 13, 0, 0),
                "beta_finished": datetime(2025, 1, 1, 13, 0, 2),
            },
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/cadence/jobs")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        assert {entry["job"] for entry in payload["jobs"]} == {"alpha", "beta"}

        beta = next(entry for entry in payload["jobs"] if entry["job"] == "beta")
        assert beta["runs_failed"] == 1
        assert beta["duration_seconds"] is not None

        limited = client.get("/api/cadence/jobs", params={"limit": 1})
        assert limited.status_code == 200
        assert len(limited.json()["jobs"]) == 1

        filtered = client.get("/api/cadence/jobs", params={"job": "beta"})
        assert filtered.status_code == 200
        filtered_jobs = filtered.json()["jobs"]
        assert len(filtered_jobs) == 1
        assert filtered_jobs[0]["job"] == "beta"

    print("PASS")