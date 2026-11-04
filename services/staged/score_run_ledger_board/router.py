"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import score_run_ledger_board

router = APIRouter(prefix="/api/scores", tags=["score-run-ledger-board"])


class JobLedgerEntry(BaseModel):
    job: str
    total_runs: int
    ok: int
    failed: int
    rows_affected_total: int
    last_started_at: str | None = None
    last_duration_seconds: float | None = None


class LedgerTotals(BaseModel):
    jobs: int
    runs: int
    failed: int


class SurfaceFreshness(BaseModel):
    surface: str
    compute_seconds_avg: float
    computed_at: str | None = None


class ScoreRunLedgerBoardResponse(BaseModel):
    jobs: list[JobLedgerEntry]
    totals: LedgerTotals
    surfaces: list[SurfaceFreshness]


@router.get("/runs/ledger", response_model=ScoreRunLedgerBoardResponse)
def get_score_run_ledger_board(
    db: Session = Depends(get_session),
) -> ScoreRunLedgerBoardResponse:
    return ScoreRunLedgerBoardResponse(**score_run_ledger_board(db))