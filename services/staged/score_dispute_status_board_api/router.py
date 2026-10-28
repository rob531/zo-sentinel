from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_status_board

router = APIRouter(prefix="/api", tags=["score-dispute-status-board"])


class StatusBoardTotals(BaseModel):
    open: int
    resolved: int
    all: int


class StatusBoardLatencyHours(BaseModel):
    mean: float
    median: float
    p90: float


class ScoreDisputeStatusBoardResponse(BaseModel):
    totals: StatusBoardTotals
    by_status: dict[str, int]
    by_reason: dict[str, int]
    latency_hours: StatusBoardLatencyHours
    stale_open_count: int


@router.get(
    "/disputes/status-board",
    response_model=ScoreDisputeStatusBoardResponse,
)
def score_dispute_status_board(
    session: Session = Depends(get_session),
) -> ScoreDisputeStatusBoardResponse:
    return get_status_board(session, McpScoreDispute)