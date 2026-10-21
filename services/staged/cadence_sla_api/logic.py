from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import CadenceJobRun


class CadenceJobSla(BaseModel):
    job: str
    runs: int
    failures: int
    success_rate: float
    last_started_at: datetime
    last_duration_seconds: Optional[float]
    avg_duration_seconds: Optional[float]
    rows_affected_last: int
    breached: bool
    detail: Dict[str, Any]


class CadenceSlaResponse(BaseModel):
    hours: int
    generated_at: datetime
    jobs: List[CadenceJobSla]
    job_count: int
    breached_count: int


router = APIRouter(prefix="/api", tags=["cadence"])


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("invalid timestamp returned by cadence_job_runs")


def _duration_expression(dialect_name: str) -> str:
    if dialect_name == "sqlite":
        return "(julianday(finished_at) - julianday(started_at)) * 86400.0"
    return "EXTRACT(EPOCH FROM (finished_at - started_at))"


def compute_cadence_sla(session: Any, hours: int = 24) -> CadenceSlaResponse:
    hours = max(1, min(720, int(hours)))
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=hours)

    dialect_name = session.bind.dialect.name
    duration_sql = _duration_expression(dialect_name)

    statement = text(
        f"""
        SELECT
            job,
            status,
            started_at,
            finished_at,
            rows_affected,
            detail,
            {duration_sql} AS duration_seconds
        FROM cadence_job_runs
        WHERE started_at >= :cutoff
        ORDER BY started_at ASC, id ASC
        """
    )
    rows = session.execute(statement, {"cutoff": cutoff}).mappings().all()

    grouped: Dict[str, List[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row["job"]), []).append(row)

    jobs: List[CadenceJobSla] = []
    for job, runs in grouped.items():
        failures = sum(
            1
            for run in runs
            if run["status"] is not None and run["status"] != "success"
        )
        newest = runs[-1]
        durations = [
            float(run["duration_seconds"])
            for run in runs
            if run["duration_seconds"] is not None
        ]
        last_duration = (
            float(newest["duration_seconds"])
            if newest["duration_seconds"] is not None
            else None
        )
        success_rate = (len(runs) - failures) / len(runs)
        avg_duration = sum(durations) / len(durations) if durations else None
        detail = newest["detail"]
        if not isinstance(detail, dict):
            detail = {}
        rows_affected = newest["rows_affected"]
        if rows_affected is None:
            rows_affected = 0

        jobs.append(
            CadenceJobSla(
                job=job,
                runs=len(runs),
                failures=failures,
                success_rate=success_rate,
                last_started_at=_as_datetime(newest["started_at"]),
                last_duration_seconds=last_duration,
                avg_duration_seconds=avg_duration,
                rows_affected_last=int(rows_affected),
                breached=success_rate < 1.0
                or (last_duration is not None and last_duration > 900.0),
                detail=detail,
            )
        )

    jobs.sort(key=lambda item: (-item.failures, item.job))

    return CadenceSlaResponse(
        hours=hours,
        generated_at=generated_at,
        jobs=jobs,
        job_count=len(jobs),
        breached_count=sum(1 for item in jobs if item.breached),
    )


@router.get("/cadence/sla", response_model=CadenceSlaResponse)
def get_cadence_sla(
    hours: int = 24,
    session: Any = Depends(get_session),
) -> CadenceSlaResponse:
    return compute_cadence_sla(session=session, hours=hours)


get_sla = get_cadence_sla
build_report = compute_cadence_sla
__all__ = [
    "CadenceJobSla",
    "CadenceSlaResponse",
    "build_report",
    "compute_cadence_sla",
    "get_cadence_sla",
    "get_sla",
    "router",
]