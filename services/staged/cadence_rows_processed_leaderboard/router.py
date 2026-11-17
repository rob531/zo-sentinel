"""HTTP surface for the cadence rows-processed leaderboard."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import rows_processed_leaderboard

router = APIRouter(
    prefix="/api/cadence",
    tags=["cadence"],
)


class JobRowsProcessed(BaseModel):
    job: str
    total_rows_affected: int
    success_runs: int
    failed_runs: int
    last_run_at: datetime | None


class RowsProcessedLeaderboardResponse(BaseModel):
    days: int
    jobs: list[JobRowsProcessed]
    grand_total_rows_affected: int
    generated_at: datetime


@router.get(
    "/rows-processed-leaderboard",
    response_model=RowsProcessedLeaderboardResponse,
)
def get_rows_processed_leaderboard(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> RowsProcessedLeaderboardResponse:
    result = rows_processed_leaderboard(db, days)
    return RowsProcessedLeaderboardResponse(**result)