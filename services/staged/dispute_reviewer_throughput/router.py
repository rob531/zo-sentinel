"""HTTP surface for dispute reviewer throughput."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_reviewer_throughput

router = APIRouter(prefix="/api", tags=["disputes"])


class ReviewerThroughput(BaseModel):
    submitted_by: str
    submitted: int
    resolved: int
    open: int
    median_resolution_hours: float
    mean_resolution_hours: float
    agreements: int


class ReviewerThroughputResponse(BaseModel):
    reviewers: list[ReviewerThroughput]


@router.get(
    "/disputes/reviewer-throughput",
    response_model=ReviewerThroughputResponse,
)
def get_reviewer_throughput_endpoint(
    db: Session = Depends(get_session),
) -> ReviewerThroughputResponse:
    result: Any = get_reviewer_throughput(db)
    reviewers = result.get("reviewers", []) if isinstance(result, dict) else result
    return ReviewerThroughputResponse(reviewers=reviewers)