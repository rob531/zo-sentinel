"""HTTP surface for analyst score-dispute pipeline health."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import dispute_pipeline_health

router = APIRouter(prefix="/api", tags=["score-dispute-pipeline-health"])


class DisputeStatusCounts(BaseModel):
    open: int
    under_review: int
    resolved: int
    rejected: int


class OldestUnresolvedDispute(BaseModel):
    server_id: str
    name: str | None
    risk_tier: str | None
    age_days: int


class PipelineHealthResponse(BaseModel):
    total: int
    by_status: DisputeStatusCounts
    unresolved: int
    median_age_days: float | None
    by_reason_category: dict[str, int]
    oldest_unresolved: OldestUnresolvedDispute | None


@router.get(
    "/disputes/pipeline-health",
    response_model=PipelineHealthResponse,
)
def get_pipeline_health(
    db: Session = Depends(get_session),
) -> PipelineHealthResponse:
    return PipelineHealthResponse(**dispute_pipeline_health(db))