"""HTTP surface for the axis escalation leaderboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_escalation_leaderboard

router = APIRouter(
    prefix="/api/scoring/axis-escalation-leaderboard",
    tags=["scoring"],
)


class TopServer(BaseModel):
    server_id: str
    name: str
    p_critical: float


class AxisEscalationEntry(BaseModel):
    axis_name: str
    escalated_to: str
    server_count: int
    top_servers: list[TopServer]


class AxisEscalationLeaderboardResponse(BaseModel):
    generated_at: str
    axes: list[AxisEscalationEntry]


@router.get(
    "",
    response_model=AxisEscalationLeaderboardResponse,
)
def get_axis_escalation_leaderboard(
    db: Session = Depends(get_session),
) -> AxisEscalationLeaderboardResponse:
    payload = axis_escalation_leaderboard(db)
    return AxisEscalationLeaderboardResponse(**payload)