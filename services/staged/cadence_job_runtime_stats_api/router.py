"""HTTP surface for cadence job runtime statistics."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import read_job_runtime_stats

router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


class JobRuntimeStats(BaseModel):
    job: str
    runs: int
    ok_runs: int
    failed_runs: int
    incomplete_runs: int
    failure_rate: float
    avg_duration_seconds: float | None
    p95_duration_seconds: float | None
    total_rows_affected: int
    last_status: str | None
    last_started_at: str | None


class RuntimeStatsResponse(BaseModel):
    days: int
    jobs: list[JobRuntimeStats]
    generated_at: str


def _runtime_stats(db: Session, days: int) -> dict:
    return read_job_runtime_stats(db, max(1, min(90, days)))


@router.get("/runtime-stats", response_model=RuntimeStatsResponse)
def get_runtime_stats(
    db: Session = Depends(get_session), days: int = 7
) -> RuntimeStatsResponse:
    return RuntimeStatsResponse(**_runtime_stats(db, days))


@router.get("/{job}/runtime-stats", response_model=RuntimeStatsResponse)
def get_job_runtime_stats(
    job: str, db: Session = Depends(get_session), days: int = 7
) -> RuntimeStatsResponse:
    result = _runtime_stats(db, days)
    result["jobs"] = [entry for entry in result["jobs"] if entry["job"] == job]
    return RuntimeStatsResponse(**result)