"""router.py -- HTTP surface for the source‑tier matrix API.

Thin wrapper that injects the real DB session via Depends(get_session) and
delegates the heavy lifting to ``services.staged.app3t_registry_source_tier_matrix_api.logic``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic module contains the actual query/aggregation implementation.
# It must expose a callable that accepts a Session and returns a dict
# matching the response schema defined below.
from .logic import source_tier_matrix

router = APIRouter(prefix="/api", tags=["registry"])


class SourceInfo(BaseModel):
    """Aggregated data for a single ``registry_source``."""
    registry_source: str = Field(..., description="Name of the registry source")
    total: int = Field(..., description="Total rows for this source")
    tiers: dict[str, int] = Field(
        ..., description="Mapping of risk tier to row count for this source"
    )
    mean_trust_score: float | None = Field(
        None,
        description="Mean trust_score across rows for this source (null if no rows)",
    )


class SourceTierMatrixResponse(BaseModel):
    """Top‑level response payload."""
    sources: list[SourceInfo] = Field(..., description="Per‑source aggregates")
    grand_total: int = Field(..., description="Total rows across all sources")


@router.get(
    "/registry/source-tier-matrix",
    response_model=SourceTierMatrixResponse,
    summary="Cross‑tab of registry source vs risk tier",
)
def get_source_tier_matrix(db: Session = Depends(get_session)) -> SourceTierMatrixResponse:
    """
    Return a matrix that cross‑references ``registry_source`` with ``risk_tier``.
    The heavy lifting is performed by :func:`source_tier_matrix`.
    """
    payload = source_tier_matrix(db)
    return SourceTierMatrixResponse(**payload)