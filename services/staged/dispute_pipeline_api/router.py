"""router.py -- dispute pipeline HTTP surface.

Exposes the API router and delegates database-backed pipeline aggregation to
logic.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_pipeline

router = APIRouter(prefix="/api", tags=["dispute_pipeline"])


class OldestOpenDispute(BaseModel):
    id: int
    server_id: str
    server_name: str | None = None
    proposed_overall_risk: Any = None
    reason_category: str | None = None
    age_hours: float


class DisputePipeline(BaseModel):
    status_counts: dict[str, int]
    reason_counts: dict[str, int]
    median_resolution_hours: float | None = None
    tier_mismatch_open: int
    oldest_open: list[OldestOpenDispute]


class DisputePipelineResponse(DisputePipeline):
    generated_at: datetime


@router.get(
    "/disputes/pipeline",
    response_model=DisputePipelineResponse,
)
def get_dispute_pipeline_endpoint(
    db: Session = Depends(get_session),
) -> DisputePipelineResponse:
    rollup = get_dispute_pipeline(db)
    if isinstance(rollup, DisputePipeline):
        data = rollup.model_dump()
    else:
        data = dict(rollup)

    data.setdefault("status_counts", {})
    data.setdefault("reason_counts", {})
    data.setdefault("median_resolution_hours", None)
    data.setdefault("tier_mismatch_open", 0)
    data.setdefault("oldest_open", [])
    data["generated_at"] = data.get("generated_at") or datetime.now(timezone.utc)
    return DisputePipelineResponse.model_validate(data)