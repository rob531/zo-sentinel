"""HTTP router for cadence rows-affected trends."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import rows_affected_trend

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class RowsAffectedPoint(BaseModel):
    date: str
    job: str
    rows_affected: int
    runs: int
    ok: int
    failed: int


class RowsAffectedTrendResponse(BaseModel):
    days: int
    jobs: list[str]
    series: list[RowsAffectedPoint]


@router.get("/rows-affected", response_model=RowsAffectedTrendResponse)
def get_rows_affected_trend(
    db: Session = Depends(get_session),
    job: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
) -> RowsAffectedTrendResponse:
    result = rows_affected_trend(db, job=job, days=days)
    return RowsAffectedTrendResponse.model_validate(result)