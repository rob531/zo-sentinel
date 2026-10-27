from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceJobRowsAffected(BaseModel):
    job: str
    runs: int = Field(ge=0)
    successes: int = Field(ge=0)
    failures: int = Field(ge=0)
    rows_total: int = Field(ge=0)
    rows_avg: float = Field(ge=0)
    zero_row_runs: int = Field(ge=0)
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None


class CadenceJobRowsAffectedResponse(BaseModel):
    window_hours: int = Field(ge=1)
    jobs: list[CadenceJobRowsAffected]


_SUCCESS_STATUSES = {
    "success",
    "succeeded",
    "successful",
    "completed",
    "complete",
    "ok",
}

_FAILURE_STATUSES = {
    "failed",
    "failure",
    "error",
    "errored",
}


def _normalized_status(status: object) -> str:
    if status is None:
        return ""
    return str(status).strip().lower()


def _rollup_runs(
    runs: Iterable[CadenceJobRun],
    window_hours: int,
) -> CadenceJobRowsAffectedResponse:
    grouped: dict[str, list[CadenceJobRun]] = {}

    for run in runs:
        job = str(run.job)
        grouped.setdefault(job, []).append(run)

    jobs: list[CadenceJobRowsAffected] = []

    for job, job_runs in grouped.items():
        runs_count = len(job_runs)
        successes = 0
        failures = 0
        rows_total = 0
        zero_row_runs = 0
        last_run: Optional[CadenceJobRun] = None

        for run in job_runs:
            status = _normalized_status(run.status)
            if status in _SUCCESS_STATUSES:
                successes += 1
            elif status in _FAILURE_STATUSES:
                failures += 1

            rows_affected = int(run.rows_affected or 0)
            rows_total += rows_affected
            if rows_affected == 0:
                zero_row_runs += 1

            if last_run is None:
                last_run = run
                continue

            if run.started_at is not None:
                if last_run.started_at is None:
                    last_run = run
                elif run.started_at > last_run.started_at:
                    last_run = run
                elif (
                    run.started_at == last_run.started_at
                    and run.id is not None
                    and last_run.id is not None
                    and run.id > last_run.id
                ):
                    last_run = run
            elif last_run.started_at is not None:
                last_run = run

        jobs.append(
            CadenceJobRowsAffected(
                job=job,
                runs=runs_count,
                successes=successes,
                failures=failures,
                rows_total=rows_total,
                rows_avg=(rows_total / runs_count) if runs_count else 0.0,
                zero_row_runs=zero_row_runs,
                last_run_at=last_run.started_at if last_run is not None else None,
                last_status=(
                    str(last_run.status)
                    if last_run is not None and last_run.status is not None
                    else None
                ),
            )
        )

    jobs.sort(key=lambda item: (-item.rows_total, item.job))

    return CadenceJobRowsAffectedResponse(
        window_hours=window_hours,
        jobs=jobs,
    )


async def get_cadence_job_rows_affected_rollup(
    hours: int,
    session: AsyncSession,
) -> CadenceJobRowsAffectedResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    statement = (
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
    )
    result = await session.execute(statement)
    runs = result.scalars().all()

    return _rollup_runs(runs, hours)


@router.get(
    "/cadence/jobs/rows-affected",
    response_model=CadenceJobRowsAffectedResponse,
)
async def cadence_job_rows_affected(
    hours: int = Query(default=24, ge=1, le=24 * 365),
    session: AsyncSession = Depends(get_session),
) -> CadenceJobRowsAffectedResponse:
    return await get_cadence_job_rows_affected_rollup(hours, session)


app = FastAPI(title="Cadence Job Rows Affected Rollup API")
app.include_router(router)


def _self_test() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/cadence/jobs/rows-affected" in paths
    print("PASS")


if __name__ == "__main__":
    _self_test()