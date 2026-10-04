# deps: fastapi, sqlalchemy, pydantic
"""Scoring Run Delta Router.

GET /api/scoring/run/delta?server_id=&days=30
  Returns p_top delta per axis for a server between the oldest and newest
  score records within the lookback window.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_scoring_deltas

router = APIRouter(prefix="/api", tags=["scoring_run_delta"])


class AxisDelta(BaseModel):
    axis_name: str
    p_top_before: float
    p_top_after: float
    delta: float
    direction_changed: bool
    escalated: bool


class ScoringDeltaResponse(BaseModel):
    server_id: str
    days: int
    runs_compared: int
    axes: list[AxisDelta]
    total_escalated_axes: int
    total_direction_changes: int


@router.get("/scoring/run/delta", response_model=ScoringDeltaResponse)
def get_scoring_run_delta(
    server_id: str = Query(..., description="Server ID to analyze"),
    days: int = Query(30, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ScoringDeltaResponse:
    result = compute_scoring_deltas(db, server_id, days)
    return ScoringDeltaResponse(**result)
