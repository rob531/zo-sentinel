from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_escalated_servers

router = APIRouter(prefix="/api", tags=["audit"])


class EscalatedAxisResponse(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    escalated_to: Optional[str] = None
    scored_at: Optional[datetime] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None


class EscalatedServerResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    last_assessed: Optional[datetime] = None
    escalated_axes: List[EscalatedAxisResponse]
    escalated_count: int


class EscalatedServersResponse(BaseModel):
    count: int
    servers: List[EscalatedServerResponse]


@router.get(
    "/audit/escalated-servers",
    response_model=EscalatedServersResponse,
)
def escalated_servers(
    limit: int = Query(default=50, ge=1, le=500),
    tier: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    return get_escalated_servers(session=session, limit=limit, tier=tier)