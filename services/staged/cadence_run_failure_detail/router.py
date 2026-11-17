"""HTTP surface for cadence run diagnostics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cadence_run, list_cadence_runs

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class CadenceRunResponse(BaseModel):
    id: int
    job: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    rows_affected: int | None = None
    detail: str | None = None


class CadenceRunListResponse(BaseModel):
    runs: list[CadenceRunResponse]
    count: int


class CadenceRunDetailResponse(BaseModel):
    run: CadenceRunResponse


@router.get("/runs", response_model=CadenceRunListResponse)
def get_runs(
    job: str | None = None,
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_session),
) -> CadenceRunListResponse:
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    runs = list_cadence_runs(db, job=job, status=status, limit=limit)
    return CadenceRunListResponse(runs=runs, count=len(runs))


@router.get("/runs/{run_id}", response_model=CadenceRunDetailResponse)
def get_run(run_id: int, db: Session = Depends(get_session)) -> CadenceRunDetailResponse:
    run = get_cadence_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Cadence run not found")
    return CadenceRunDetailResponse(run=run)