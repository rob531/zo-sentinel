from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_cadence_run_failures

router = APIRouter(prefix="/api", tags=["cadence-run-failures"])


class JobFailureMetrics(BaseModel):
    job: str
    runs: int
    failures: int
    failure_rate: float
    median_duration_s: float
    p95_duration_s: float
    last_started_at: str


class CadenceRunFailureResponse(BaseModel):
    days: int
    total_runs: int
    failed_runs: int
    failure_rate: float
    by_job: list[JobFailureMetrics]
    last_error: str
    last_failure_at: str


@router.get(
    "/cadence/runs/failures",
    response_model=CadenceRunFailureResponse,
)
def cadence_run_failures(
    days: int = Query(default=14, ge=1),
    db: Session = Depends(get_session),
) -> CadenceRunFailureResponse:
    return CadenceRunFailureResponse.model_validate(
        get_cadence_run_failures(db, days)
    )