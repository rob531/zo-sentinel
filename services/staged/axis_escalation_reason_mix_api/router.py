from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_escalation_mix

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class EscalationAxisBucket(BaseModel):
    axis_name: str
    escalated_to: Optional[str]
    count: int
    distinct_servers: int
    median_p_critical: Optional[float]


class RecentEscalation(BaseModel):
    server_id: str
    server_name: Optional[str]
    axis_name: str
    escalated_to: Optional[str]
    p_critical: Optional[float]
    scored_at: datetime


class EscalationMixResponse(BaseModel):
    days: int
    total_escalations: int
    by_axis: List[EscalationAxisBucket]
    recent: List[RecentEscalation]


@router.get("/escalation-mix", response_model=EscalationMixResponse)
def escalation_mix(
    days: int = Query(30),
    session: Session = Depends(get_session),
) -> EscalationMixResponse:
    if days < 1 or days > 365:
        raise HTTPException(
            status_code=400,
            detail="days must be between 1 and 365",
        )

    result = get_escalation_mix(session=session, days=days)
    return EscalationMixResponse(**result)