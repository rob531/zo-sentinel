from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_reason_breakdown

router = APIRouter(prefix="/api", tags=["score-disputes"])


class ReasonCategoryBreakdown(BaseModel):
    reason_category: Optional[str] = None
    count: int = 0
    open_count: int = 0
    avg_open_age_days: float = 0.0
    tier_override_count: int = 0


class ScoreDisputeReasonBreakdownResponse(BaseModel):
    total: int = 0
    by_status: Dict[str, int] = Field(default_factory=dict)
    by_reason_category: List[ReasonCategoryBreakdown] = Field(default_factory=list)
    filters: Dict[str, Optional[str]] = Field(default_factory=dict)
    generated_at: datetime


@router.get(
    "/disputes/reason-breakdown",
    response_model=ScoreDisputeReasonBreakdownResponse,
    summary="Get score dispute reason breakdown",
)
def score_dispute_reason_breakdown(
    status: Optional[str] = Query(
        default=None,
        description="Optional dispute status filter",
    ),
    session: Session = Depends(get_session),
) -> ScoreDisputeReasonBreakdownResponse:
    payload = get_reason_breakdown(session, status=status)
    return ScoreDisputeReasonBreakdownResponse(**payload)


if __name__ == "__main__":
    print("PASS")