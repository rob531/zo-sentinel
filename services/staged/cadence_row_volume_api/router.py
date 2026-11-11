from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cadence_row_volume

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class JobRowVolume(BaseModel):
    job: str
    run_count: int
    success_count: int
    failure_count: int
    total_rows_affected: int
    avg_rows_affected: float
    avg_duration_seconds: float


class CadenceRowVolumeResponse(BaseModel):
    jobs: list[JobRowVolume]
    total_jobs: int


@router.get("/row-volume", response_model=CadenceRowVolumeResponse)
def cadence_row_volume(
    job: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> CadenceRowVolumeResponse:
    payload = get_cadence_row_volume(session, job=job)
    return CadenceRowVolumeResponse.model_validate(payload)