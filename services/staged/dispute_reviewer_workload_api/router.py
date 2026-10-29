from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from .logic import get_reviewer_workload

router = APIRouter(prefix="/api", tags=["disputes"])


class ReasonCategoryCount(BaseModel):
    reason_category: Optional[str] = None
    count: int = Field(ge=0)


class ReviewerWorkload(BaseModel):
    submitted_by: Optional[str] = None
    submitted: int = Field(ge=0)
    resolved: int = Field(ge=0)
    open: int = Field(ge=0)
    median_resolution_hours: Optional[float] = None


class ReviewerWorkloadResponse(BaseModel):
    total: int = Field(ge=0)
    by_status: Dict[str, int]
    reviewers: List[ReviewerWorkload]
    tier_change_proposals: int = Field(ge=0)
    reason_categories: List[ReasonCategoryCount]


@router.get(
    "/disputes/reviewer-workload",
    response_model=ReviewerWorkloadResponse,
)
def reviewer_workload(
    status: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
) -> ReviewerWorkloadResponse:
    return get_reviewer_workload(
        session=session,
        status=status,
        days=days,
    )