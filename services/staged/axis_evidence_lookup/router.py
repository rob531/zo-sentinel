"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# The logic layer implements the actual DB query.
from .logic import fetch_axis_evidence

router = APIRouter(prefix="/api", tags=["axis_evidence_lookup"])


class AxisInfo(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: int
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    escalated_to: Optional[str] = None
    scored_at: Optional[datetime] = None


class ServerAxisEvidenceResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    criteria_version: Optional[str] = None
    axes: List[AxisInfo]


@router.get(
    "/servers/{server_id}/axis-evidence",
    response_model=ServerAxisEvidenceResponse,
    responses={404: {"description": "Server not found"}},
)
def get_axis_evidence(
    server_id: str,
    axis: Optional[str] = None,
    include_history: bool = False,
    db: Session = Depends(get_session),
) -> ServerAxisEvidenceResponse:
    """
    Retrieve raw per‑axis LLM scores for a given server.

    Parameters
    ----------
    server_id: str
        Identifier of the server.
    axis: Optional[str]
        If provided, filter results to this axis name.
    include_history: bool
        If true, include historical rows; otherwise only the newest per axis.
    db: Session
        Injected SQLAlchemy session.

    Returns
    -------
    ServerAxisEvidenceResponse
        Aggregated server metadata and a list of axis score records.
    """
    return fetch_axis_evidence(db, server_id, axis, include_history)