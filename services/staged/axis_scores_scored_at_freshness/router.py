"""router.py -- HTTP surface for the axis_scores_scored_at_freshness service.

Thin wrapper that injects a real SQLAlchemy Session via Depends(get_session)
and delegates all business logic to ``services.staged.axis_scores_scored_at_freshness.logic``.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The service's core computation lives in ``logic.py``.
# It returns a dict matching the response model defined below.
from .logic import axis_scores_freshness

router = APIRouter(prefix="/api", tags=["axis_scores_scored_at_freshness"])


class Bucket(BaseModel):
    label: str = Field(..., description="Human‑readable bucket label")
    count: int = Field(..., description="Number of rows in the bucket")
    servers: int = Field(..., description="Distinct server count in the bucket")


class AxisInfo(BaseModel):
    axis_name: str = Field(..., description="Axis enum value")
    latest_scored_at: datetime | None = Field(
        None, description="Most recent scored_at for this axis"
    )
    count_24h: int = Field(..., description="Rows for this axis scored within the last 24 h")


class FreshnessResponse(BaseModel):
    hours: int = Field(..., description="Age window (hours) used for the query")
    buckets: List[Bucket] = Field(..., description="Aggregated bucket statistics")
    axes: List[AxisInfo] = Field(..., description="Per‑axis breakdown")
    latest_scored_at: datetime | None = Field(
        None, description="Most recent scored_at across all axes"
    )


@router.get(
    "/axis/scored-at-freshness",
    response_model=FreshnessResponse,
    summary="Summarise how recently axis scores were written",
)
def get_scored_at_freshness(
    hours: int = Query(
        24,
        ge=1,
        description="Age window in hours for which to compute freshness statistics",
    ),
    db: Session = Depends(get_session),
) -> FreshnessResponse:
    """
    Return freshness statistics for MCP LLM axis scores.

    The heavy lifting is performed by :func:`axis_scores_freshness` which
    receives the SQLAlchemy session and the ``hours`` window and returns a
    mapping compatible with :class:`FreshnessResponse`.
    """
    result = axis_scores_freshness(db, hours)
    return FreshnessResponse(**result)