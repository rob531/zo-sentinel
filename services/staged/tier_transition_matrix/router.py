"""router.py -- the service's HTTP surface (one concern, one file).

Thin wrapper that injects the real Session via Depends(get_session) and
delegates the heavy lifting to the sibling logic module.  All intra‑service
imports are relative so the module works identically in staged and active
deployments.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic module provides the core computation.
from .logic import compute_tier_transition_matrix

router = APIRouter(prefix="/api", tags=["tier_transition_matrix"])


class TierTransitionMatrixResponse(BaseModel):
    """Response model for the tier‑transition‑matrix endpoint."""

    days: int = Field(..., description="Number of days looked back")
    server_id: Optional[int] = Field(
        None, description="If supplied, limits the matrix to a single server"
    )
    tiers: List[str] = Field(..., description="All risk‑tier names observed")
    matrix: Dict[str, Dict[str, int]] = Field(
        ..., description="Counts of transitions from one tier to another"
    )
    totals: Dict[str, int] = Field(..., description="Total occurrences per tier")
    generated_at: datetime = Field(..., description="Timestamp of generation")


@router.get(
    "/tier-transition-matrix",
    response_model=TierTransitionMatrixResponse,
    summary="Risk‑tier transition matrix",
)
def get_tier_transition_matrix(
    days: int = Query(30, ge=1, description="Look‑back window in days"),
    server_id: Optional[int] = Query(
        None, description="Optional server identifier to filter the matrix"
    ),
    db: Session = Depends(get_session),
) -> TierTransitionMatrixResponse:
    """
    Return a matrix describing how servers have moved between risk tiers over the
    requested time window.  The heavy lifting is performed by
    ``services.staged.tier_transition_matrix.logic.compute_tier_transition_matrix``.
    """
    result = compute_tier_transition_matrix(db=db, days=days, server_id=server_id)

    # ``result`` is expected to be a mapping with the exact keys required by the
    # response model.
    return TierTransitionMatrixResponse(
        days=result["days"],
        server_id=result.get("server_id"),
        tiers=result["tiers"],
        matrix=result["matrix"],
        totals=result["totals"],
        generated_at=result["generated_at"],
    )