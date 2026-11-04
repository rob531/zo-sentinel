"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra‑service imports are RELATIVE (`from .logic import …`) so the module
works identically at services.staged.<name> and services.active.<name> –
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_data_sensitivity

router = APIRouter(prefix="/api", tags=["axis_data_sensitivity"])


class AxisRow(BaseModel):
    server_id: str
    name: Optional[str] = None
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    escalated_to: Optional[str] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[str] = None
    risk_tier: Optional[str] = None


class AxisDataSensitivityResponse(BaseModel):
    axis: str
    rows: List[AxisRow]
    count: int


@router.get(
    "/axis/data_sensitivity",
    response_model=AxisDataSensitivityResponse,
    summary="Get data‑sensitivity scores for servers",
)
def get_data_sensitivity(
    server_id: Optional[str] = Query(
        default=None, description="Filter to a single server identifier"
    ),
    limit: int = Query(
        default=50,
        le=500,
        description="Maximum number of rows to return (max 500)",
    ),
    db: Session = Depends(get_session),
) -> AxisDataSensitivityResponse:
    """
    Retrieve rows from `mcp_llm_axis_scores` where `axis_name='data_sensitivity'`,
    joined with `mcp_server_registry`. Results are ordered by `p_top` DESC then
    `scored_at` DESC. Optional `server_id` filters to a single server.
    """
    payload = get_axis_data_sensitivity(db, server_id=server_id, limit=limit)
    # The logic layer returns a dict compatible with the response model.
    return AxisDataSensitivityResponse(**payload)