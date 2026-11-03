"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.axis_drift_history and services.active.axis_drift_history.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import fetch_axis_history

router = APIRouter(prefix="/api", tags=["axis_drift_history"])


class Point(BaseModel):
    scored_at: datetime
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str] = None
    model_version: str
    decision_rule_version: str


class AxisData(BaseModel):
    label: str
    points: List[Point]
    delta_p_top: float
    max_p_top: float
    escalated_count: int


class AxisHistoryResponse(BaseModel):
    server_id: str
    axes: Dict[str, AxisData]
    axes_present: int
    latest_model_version: str


@router.get(
    "/servers/{server_id}/axis-history",
    response_model=AxisHistoryResponse,
)
def get_axis_history(
    server_id: str,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> AxisHistoryResponse:
    """
    Return the historical LLM axis scores for a given server.

    The underlying logic fetches rows from `mcp_llm_axis_scores`,
    aggregates them per known axis, and computes deltas and counts.
    """
    raw = fetch_axis_history(db, server_id, limit)
    return AxisHistoryResponse(**raw)